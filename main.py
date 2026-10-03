from machine import Pin, SPI, WDT, UART
from writer import Writer
import utime
import _thread

import l76x
import NMEA
import sh1107
import roboto14

# Variables
DEBUG = True                      # echo forwarded sentences to the REPL
SCREEN_REFRESH_RATE = 0.5 * 1000  # how often to refresh screen
STATS_REFRESH_RATE = 10 * 1000    # how often (in milliseconds) stats will be refreshed
STATS_MULTIPLIER = 60 * 1000 / STATS_REFRESH_RATE # multiplier to get stats per minute
LONG_PRESS_THRESHOLD = 1 * 1000   # threshold in milliseconds to distinguish between short and long press
WATCHDOG_TIMEOUT = 5 * 1000       # watchdog has to be fed every N milliseconds
GPS_SILENCE_TIMEOUT = 30 * 1000   # stop feeding watchdog (=> reset) if no GPS data for N milliseconds
UARTx = 0                         # GPS UART
BAUDRATE = 9600                   # GPS baudrate

# Pins and buses
PIN_OLED_SCK, PIN_OLED_MOSI = 10, 11
PIN_OLED_DC, PIN_OLED_RST, PIN_OLED_CS = 8, 12, 9
OLED_SPI_BAUDRATE = 10_000_000
PIN_KEY_UP, PIN_KEY_DN = 15, 17
RS485_UART = 1                    # must differ from UARTx when both use UART1 pins
RS485_TX, RS485_RX = 4, 5
RS485_BAUDRATE = 4800             # what the VHF radio expects

# Display
spi1 = SPI(1, baudrate=OLED_SPI_BAUDRATE, sck=Pin(PIN_OLED_SCK), mosi=Pin(PIN_OLED_MOSI), miso=None)
oled = sh1107.SH1107_SPI(128, 64, spi1, Pin(PIN_OLED_DC), Pin(PIN_OLED_RST), Pin(PIN_OLED_CS), rotate=180)
font_large = Writer(oled, roboto14)
oled.init_display()
oled.fill(0)
oled.text('Waiting for fix...', 0, 0, 1)
oled.show()

# Key handling
key0 = Pin(PIN_KEY_UP, Pin.IN, Pin.PULL_UP)
key1 = Pin(PIN_KEY_DN, Pin.IN, Pin.PULL_UP)

SCREEN_MAIN, SCREEN_STATS, SCREEN_DEBUG = 0, 1, 2
screen = SCREEN_MAIN
screen_max = SCREEN_STATS         # short presses cycle 0..screen_max; debug is long-press only

press_time = {PIN_KEY_UP: 0, PIN_KEY_DN: 0}

def step_screen(direction):
    global screen
    screen = (screen + direction) % (screen_max + 1)

def make_button_handler(key_id, direction, long_target):
    def handler(pin):
        global screen
        if pin.value() == 0:  # pressed (active low)
            press_time[key_id] = utime.ticks_ms()
        else:                 # released
            if utime.ticks_diff(utime.ticks_ms(), press_time[key_id]) >= LONG_PRESS_THRESHOLD:
                screen = long_target
            else:
                step_screen(direction)
    return handler

# Register the handler functions for both rising and falling edges
key0.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=make_button_handler(PIN_KEY_UP, +1, SCREEN_DEBUG))
key1.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=make_button_handler(PIN_KEY_DN, -1, SCREEN_MAIN))

# Threading
rx_queue = []                     # complete sentences waiting for the main loop
RX_QUEUE_MAX = 16                 # drop oldest beyond this
MAX_SENTENCE_LEN = 100            # longer buffers are garbage; resync on next '$'
mutex = _thread.allocate_lock()

def gps_thread():

    # GPS init
    gps=l76x.L76X(uartx = UARTx, _baudrate = BAUDRATE)
    gps.send_command(gps.PMTK_API_SET_STOP_QZSS) # disable Japanese QZSS
    gps.send_command(gps.PMTK_API_SET_SBAS_ENABLED)
    gps.send_command(gps.PMTK_API_SET_DGPS_MODE)
    gps.send_command(gps.PMTK_ENABLE_EASY) 
    gps.send_command(gps.SET_POS_FIX_800MS)
    gps.send_command(gps.SET_NORMAL_MODE)
    gps.send_command(gps.SET_GPS_SEARCH_MODE)
    gps.send_command(gps.SET_SYNC_PPS_NMEA_ON)
    gps.send_command(gps.SET_NMEA_OUTPUT)
    gps.send_command(gps.SET_NMEA_BAUDRATE_9600)
    
    buffer = bytearray()
    while True:
        n = gps.uart_any()
        if not n:
            utime.sleep_ms(2)
            continue
        try:
            data = gps.uart_receive_string(n)
        except OSError:
            continue
        if not data:
            continue
        for ascii_char in data:
            if not 10 <= ascii_char <= 126:
                continue
            if ascii_char == 0x24:  # '$' - beginning of a new sentence
                if buffer:
                    sentence = buffer.decode()
                    mutex.acquire()
                    if len(rx_queue) >= RX_QUEUE_MAX:
                        rx_queue.pop(0)
                    rx_queue.append(sentence)
                    mutex.release()
                buffer = bytearray()
            if len(buffer) < MAX_SENTENCE_LEN:
                buffer.append(ascii_char)

_thread.start_new_thread(gps_thread, ())

def gather_stats(stats):
    stats['rcv'] = nmea_parser.sentences_received
    stats['rcvpm'] = stats['rcv'] * STATS_MULTIPLIER # received per minute
    stats['val'] = nmea_parser.sentences_valid
    stats['inv'] = nmea_parser.sentences_invalid
    stats['par'] = nmea_parser.sentences_parsed
    stats['ign'] = nmea_parser.sentences_ignored
    # Reset stats
    nmea_parser.sentences_received = 0
    nmea_parser.sentences_valid = 0
    nmea_parser.sentences_invalid = 0
    nmea_parser.sentences_parsed = 0
    nmea_parser.sentences_ignored = 0
    # TODO: test resets below, maybe one wants global stats after all
    nmea_parser.sentence_last_valid_type = ''
    nmea_parser.sentence_last_invalid_type = ''
    nmea_parser.sentence_last_ignored_type = ''
    nmea_parser.sentence_last_parsed_type = ''   
    return stats

#
# MAIN LOOP
#
nmea_parser = NMEA.Parser()
stats = { 'rcvpm' : 1, 'rcv' : 1, 'val' : 0, 'inv' : 0, 'par' : 0, 'ign' : 0 }
last_display_update = utime.ticks_ms()

# RS-485 setup
uart = UART(RS485_UART, baudrate=RS485_BAUDRATE, tx=Pin(RS485_TX), rx=Pin(RS485_RX))
uart.init(RS485_BAUDRATE, bits=8, parity=None, stop=1)
utime.sleep(1) # grace time for UART to start

# Watchdog
last_stats = utime.ticks_ms()
last_gps_rx = utime.ticks_ms()
wdt = WDT(timeout = WATCHDOG_TIMEOUT)

# Main loop
while True:
    # Feed watchdog only while GPS data keeps arriving
    if utime.ticks_diff(utime.ticks_ms(), last_gps_rx) < GPS_SILENCE_TIMEOUT:
        wdt.feed()

    # Take pending sentences under the lock, parse outside it
    mutex.acquire()
    pending = rx_queue[:]
    del rx_queue[:]
    mutex.release()
    if pending:
        last_gps_rx = utime.ticks_ms()

    for buffer in pending:
        if nmea_parser.parse_sentence(buffer):
            # forward the (fixed) sentence to the VHF radio, once
            uart.write(nmea_parser.last_valid_sentence)
            if DEBUG:
                print(nmea_parser.last_valid_sentence.strip())

    # Gather stats every STATS_REFRESH_RATE milliseconds
    if utime.ticks_diff(utime.ticks_ms(), last_stats) > STATS_REFRESH_RATE:
        stats = gather_stats(stats)
        last_stats = utime.ticks_ms()

    # Update the OLED display only every N ms
    if utime.ticks_diff(utime.ticks_ms(), last_display_update) > SCREEN_REFRESH_RATE:
        oled.fill(0)
        if screen == SCREEN_MAIN:
            oled.text(nmea_parser.get_time_string() + ' GMT', 30, 3, 1)
            oled.hline(0, 14, 128, 1)
            Writer.set_textpos(oled, 17, 0)
            font_large.printstring(nmea_parser.get_lat_string())
            Writer.set_textpos(oled, 32, 0)
            font_large.printstring(nmea_parser.get_lon_string())
            oled.hline(0, 48, 128, 1)
            oled.text(nmea_parser.fix_type + ' ' + nmea_parser.mode + ' ' + 
                      str(nmea_parser.birds_in_use) + '/' + str(nmea_parser.birds_in_view), 0, 54, 1)
            oled.text(nmea_parser.get_dop_string(type='PDOP') + nmea_parser.get_dop_string(type='HDOP') + nmea_parser.get_dop_string(type='VDOP'), 104, 54, 1)
        elif screen == SCREEN_STATS:
            rcv = stats['rcv'] or 1  # avoid division by zero in empty windows
            oled.text("rcv: " + str(round(stats['rcvpm'])) + "/min", 0, 0 * 12, 1)
            oled.text("val: " + str(round(stats['val']/rcv * 100)) + '% ' + nmea_parser.sentence_last_valid_type, 0, 1 * 12, 1)
            oled.text("inv: " + str(round(stats['inv']/rcv * 100)) + '% ' + nmea_parser.sentence_last_invalid_type, 0, 2 * 12, 1)
            oled.text("par: " + str(round(stats['par']/rcv * 100)) + '% ' + nmea_parser.sentence_last_parsed_type, 0, 3 * 12, 1)
            oled.text("ign: " + str(round(stats['ign']/rcv * 100)) + '% ' + nmea_parser.sentence_last_ignored_type, 0, 4 * 12, 1)
        elif screen == SCREEN_DEBUG:
            oled.text(nmea_parser.last_valid_sentence.strip()[:16], 0, 0, 1)
            oled.text(nmea_parser.date, 0, 10, 1)
            oled.hline(0,20,128,1)
            oled.text('GPS:' + str(nmea_parser.birds_GPS), 0, 24, 1)
            oled.text('SBAS:' + str(nmea_parser.birds_SBAS), 0, 34, 1)
            oled.text('GLONASS:' + str(nmea_parser.birds_GLONASS), 0, 44, 1)
            oled.text('OTHER:' + str(nmea_parser.birds_OTHER), 0, 54, 1)
            oled.hline(0,63,128,1)
        oled.show()
        last_display_update = utime.ticks_ms()

    utime.sleep(0.01) # yield so the GPS thread isn't starved



