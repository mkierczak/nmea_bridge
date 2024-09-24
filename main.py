from machine import Pin, SPI, WDT, UART
from writer import Writer
import utime
import _thread
import copy

import network
import socket

import l76x
import NMEA
import sh1107
import roboto14

# Vriables
DEBUG = True 					# TODO: currently not in use
SCREEN_REFRESH_RATE = 0.5 * 1000  # how often to refresh screen
STATS_REFRESH_RATE = 10 * 1000	# how often (in milliseconds) stats will be refreshed
STATS_MULTIPLIER = 6 * 1000 / STATS_REFRESH_RATE # multiplier to get stats per minute
LONG_PRESS_THRESHOLD = 1 * 1000	# threshold in milliseconds to distinguish between short and long press
WATCHDOG_TIMEOUT = 5 * 1000 	# watchdog has to be fed every N seconds
RCVPM_THRESHOLD = 10 			# watchdog - at least N messages from GPS have to be received per minute
INV_THRESHOLD = 80 				# watchdog - if more than N per-cent messages are invalid -- reset
UARTx = 0	# GPS UART
BAUDRATE = 9600	# GPS baudrate

# Display
spi1 = SPI(1, baudrate=10_000_000, sck=Pin(10), mosi=Pin(11), miso=None) # SCK MOSI MISO
oled = sh1107.SH1107_SPI(128, 64, spi1, Pin(8), Pin(12), Pin(9), rotate=180) # DC RST CS
font_large = Writer(oled, roboto14)
oled.init_display()
oled.fill(0)
oled.text('Waiting for fix...', 0, 0, 1)
oled.show()

# Key handling
key0 = Pin(15, Pin.IN, Pin.PULL_UP)
key1 = Pin(17, Pin.IN, Pin.PULL_UP)

screen = 0
screen_max = 1

# Variables to store button press times
key0_press_time = 0
key1_press_time = 0

def button_UP_handler(pin):
    global key0_press_time
    if pin.value() == 0:  # Button is pressed (active low)
        key0_press_time = utime.ticks_ms()
    else:  # Button is released
        press_duration = utime.ticks_diff(utime.ticks_ms(), key0_press_time)
        if press_duration >= LONG_PRESS_THRESHOLD:
            handle_long_press_UP()
        else:
            handle_short_press_UP()

def button_DN_handler(pin):
    global key1_press_time
    if pin.value() == 0:  # Button is pressed (active low)
        key1_press_time = utime.ticks_ms()
    else:  # Button is released
        press_duration = utime.ticks_diff(utime.ticks_ms(), key1_press_time)
        if press_duration >= LONG_PRESS_THRESHOLD:
            handle_long_press_DN()
        else:
            handle_short_press_DN()

def handle_short_press_UP():
    global screen
    if screen >= screen_max:
        screen = 0
    else:
        screen += 1

def handle_long_press_UP():
    global screen
    screen = 2

def handle_short_press_DN():
    global screen
    if screen <= 0:
        screen = screen_max
    else:
        screen -= 1

def handle_long_press_DN():
    global screen
    screen = 0

# Register the handler functions for both rising and falling edges
key0.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=button_UP_handler)
key1.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=button_DN_handler)

# Threading
received_sentence = ''
mutex = _thread.allocate_lock()

def gps_thread():
    global received_sentence
    
    # GPS init
    gps=l76x.L76X(uartx=UARTx,_baudrate = BAUDRATE)
    #gps.l76x_send_command(gps.SET_FULL_COLD_START)
    gps.send_command(gps.SET_POS_FIX_800MS)
    gps.send_command(gps.SET_NORMAL_MODE)
    gps.send_command(gps.SET_GPS_SEARCH_MODE)
    gps.send_command(gps.SET_SYNC_PPS_NMEA_ON)
    gps.send_command(gps.SET_NMEA_OUTPUT)
    gps.send_command(gps.SET_NMEA_BAUDRATE_9600)
    
    buffer = ''
    while True:
        if gps.uart_any():
            try:
                ascii_char = gps.uart_receive_byte()[0]
                if 10 <= ascii_char <= 126:
                    char = chr(ascii_char)
                    if char == '$':  # Beginning of a new sentence
                        #print(f'Parsing: {buffer.strip()}')
                        #nmea_parser.parse_sentence(buffer)
                        mutex.acquire()
                        received_sentence = buffer
                        mutex.release()
                        buffer = ''
                    buffer += char                
            except:
                pass

thread1 = _thread.start_new_thread(gps_thread, ())

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
    return stats

#
# MAIN LOOP
#
nmea_parser = NMEA.parser()
mode = 0					# TODO: 0 - normal mode, 1 - service mode
screen = 0
buffer = ''
stats = { 'rcvpm' : 1, 'rcv' : 1, 'val' : 0, 'inv' : 0, 'par' : 0, 'ign' : 0 }
last_display_update = utime.ticks_ms()
oled.init_display()

# RS-485 setup
uart = UART(1, baudrate=4800, tx=Pin(4), rx=Pin(5))
uart.init(4800, bits=8, parity=None, stop=1)
utime.sleep(1) # grace time for UART to start

# Watchdog
last_stats = utime.ticks_ms()
wdt = WDT(timeout = WATCHDOG_TIMEOUT)

# Main loop
while True:
    # Feed watchdog
    wdt.feed()

    # Handle buffer with mutex
    try:
        mutex.acquire()
        buffer = copy.copy(received_sentence)
        if len(buffer) > 0:
            nmea_parser.parse_sentence(buffer)
    finally:
        mutex.release()

    # resend buffer to the VHF radio
    txData = nmea_parser.last_valid_sentence
    print(txData.strip())
    uart.write(txData)

    # Gather stats every STATS_REFRESH_RATE milliseconds
    if utime.ticks_diff(utime.ticks_ms(), last_stats) > STATS_REFRESH_RATE:
        stats = gather_stats(stats)
        last_stats = utime.ticks_ms()

    # Update the OLED display only every N ms
    if utime.ticks_diff(utime.ticks_ms(), last_display_update) > SCREEN_REFRESH_RATE:
        # resend buffer to the VHF radio
        txData = nmea_parser.last_valid_sentence
        print(buffer.strip())
        uart.write(buffer.strip() + '\n')

        oled.fill(0)
        if screen == 0:  # Main screen
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
        elif screen == 1: # Stats screen
            oled.fill(0)
            oled.text("rcv: " + str(stats['rcv']) + "/min", 0, 0 * 12, 1)
            oled.text("val: " + str(round(stats['val']/stats['rcv'] * 100)) + '% ' + nmea_parser.sentence_last_valid_type, 0, 1 * 12, 1)
            oled.text("inv: " + str(round(stats['inv']/stats['rcv'] * 100)) + '% ' + nmea_parser.sentence_last_invalid_type, 0, 2 * 12, 1)
            oled.text("par: " + str(round(stats['par']/stats['rcv'] * 100)) + '% ' + nmea_parser.sentence_last_parsed_type, 0, 3 * 12, 1)
            oled.text("ign: " + str(round(stats['ign']/stats['rcv'] * 100)) + '% ' + nmea_parser.sentence_last_ignored_type, 0, 4 * 12, 1)        
        elif screen == 2: # Debug screen
            oled.fill(0)
            oled.text(buffer, 0, 0, 1)
            oled.text('Var:' + nmea_parser.magvar, 0, 10, 1)
            oled.hline(0,20,128,1)
            oled.text('GPS:' + str(nmea_parser.birds_GPS), 0, 24, 1)
            oled.text('SBAS:' + str(nmea_parser.birds_SBAS), 0, 34, 1)
            oled.text('GLONASS:' + str(nmea_parser.birds_GLONASS), 0, 44, 1)
            oled.text('OTHER:' + str(nmea_parser.birds_OTHER), 0, 54, 1)
            oled.hline(0,63,128,1)
        oled.show()
        last_display_update = utime.ticks_ms()




