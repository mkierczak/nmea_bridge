from machine import Pin, SPI, WDT, UART
from writer import Writer
import utime
import _thread

import l76x
import NMEA
import screens
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
FORWARD_TYPES = ('RMC', 'GGA', 'GSA', 'GSV', 'ZDA')  # sentence types forwarded to the radio
FIX_STALE_TIMEOUT = 10 * 1000     # show NO FIX if no position update for N milliseconds
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
RS485_TXBUF = 512                 # TX buffer so uart.write doesn't block the main loop

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

SCREEN_MAIN, SCREEN_STATS, SCREEN_DEBUG = screens.SCREEN_MAIN, screens.SCREEN_STATS, screens.SCREEN_DEBUG
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
rx_dropped = 0                    # sentences dropped because the queue was full
RX_QUEUE_MAX = 16                 # drop oldest beyond this
MAX_SENTENCE_LEN = 100            # longer buffers are garbage; resync on next '$'
mutex = _thread.allocate_lock()

def gps_thread():
    global rx_dropped

    # GPS init
    gps = l76x.L76X(uartx=UARTx, _baudrate=BAUDRATE, verbose=DEBUG)
    gps.send_command(gps.PMTK_API_SET_STOP_QZSS) # disable Japanese QZSS
    gps.send_command(gps.PMTK_API_SET_SBAS_ENABLED)
    gps.send_command(gps.PMTK_API_SET_DGPS_MODE)
    gps.send_command(gps.PMTK_ENABLE_EASY) 
    gps.send_command(gps.SET_POS_FIX_800MS)
    gps.send_command(gps.SET_NORMAL_MODE)
    gps.send_command(gps.SET_GPS_SEARCH_MODE)
    gps.send_command(gps.SET_SYNC_PPS_NMEA_ON)
    gps.send_command(gps.SET_NMEA_OUTPUT)
    
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
                        rx_dropped += 1
                    rx_queue.append(sentence)
                    mutex.release()
                buffer = bytearray()
            if len(buffer) < MAX_SENTENCE_LEN:
                buffer.append(ascii_char)

_thread.start_new_thread(gps_thread, ())

def gather_stats():
    stats = nmea_parser.snapshot_and_reset()
    stats['rcvpm'] = stats['rcv'] * STATS_MULTIPLIER # received per minute
    return stats

#
# MAIN LOOP
#
nmea_parser = NMEA.Parser()
stats = { 'rcvpm' : 1, 'rcv' : 1, 'val' : 0, 'inv' : 0, 'par' : 0, 'ign' : 0 }
last_display_update = utime.ticks_ms()

# RS-485 setup
uart = UART(RS485_UART, baudrate=RS485_BAUDRATE, tx=Pin(RS485_TX), rx=Pin(RS485_RX), txbuf=RS485_TXBUF)
uart.init(RS485_BAUDRATE, bits=8, parity=None, stop=1)
utime.sleep(1) # grace time for UART to start

# Watchdog
last_stats = utime.ticks_ms()
last_gps_rx = utime.ticks_ms()
last_pos = None                   # ticks of last valid GGA/RMC
wdt = None                        # armed on first GPS sentence (can't be stopped once started)
last_sig = None

# Main loop
while True:
    # Feed watchdog only while GPS data keeps arriving
    if wdt and utime.ticks_diff(utime.ticks_ms(), last_gps_rx) < GPS_SILENCE_TIMEOUT:
        wdt.feed()

    # Take pending sentences under the lock, parse outside it
    mutex.acquire()
    pending = rx_queue[:]
    del rx_queue[:]
    mutex.release()
    if pending:
        last_gps_rx = utime.ticks_ms()
        if wdt is None:
            wdt = WDT(timeout=WATCHDOG_TIMEOUT)

    for buffer in pending:
        if nmea_parser.parse_sentence(buffer):
            sentence_type = nmea_parser.sentence_last_valid_type
            if sentence_type in ('GGA', 'RMC'):
                last_pos = utime.ticks_ms()
            if sentence_type in FORWARD_TYPES:
                # forward the (fixed) sentence to the VHF radio, once
                uart.write(nmea_parser.last_valid_sentence)
                if DEBUG:
                    print(nmea_parser.last_valid_sentence.strip())

    # Gather stats every STATS_REFRESH_RATE milliseconds
    if utime.ticks_diff(utime.ticks_ms(), last_stats) > STATS_REFRESH_RATE:
        stats = gather_stats()
        last_stats = utime.ticks_ms()

    # Update the OLED only every N ms, and only if something visible changed
    if utime.ticks_diff(utime.ticks_ms(), last_display_update) > SCREEN_REFRESH_RATE:
        no_fix = (nmea_parser.fix_type == 'NO' or last_pos is None or
                  utime.ticks_diff(utime.ticks_ms(), last_pos) > FIX_STALE_TIMEOUT)
        sig = (screen, no_fix, rx_dropped, tuple(stats.values()), nmea_parser.display_signature())
        if sig != last_sig:
            screens.draw(oled, font_large, screen, nmea_parser, stats, rx_dropped, no_fix)
            oled.show()
            last_sig = sig
        last_display_update = utime.ticks_ms()

    utime.sleep(0.01) # yield so the GPS thread isn't starved
