from machine import Pin, SPI
from writer import Writer
import utime
import _thread
import copy

import l76x
import NMEA
import sh1107
import roboto14 #dogica_gps

# Vriables
DEBUG = True

# Threading
received_sentence = ''
mutex = _thread.allocate_lock()

# Display
spi1 = SPI(1, baudrate=1_000_000, sck=Pin(10), mosi=Pin(11), miso=None) # SCK MOSI MISO
OLED = sh1107.SH1107_SPI(128, 64, spi1, Pin(8), Pin(12), Pin(9), rotate=180) # DC RST CS
font_large = Writer(OLED, roboto14)
OLED.init_display()
OLED.fill(0)
OLED.text('Waiting for GPS...', 0, 0, 1)
OLED.show()

#gps.set_baudrate(BAUDRATE)
#gps.send_command('$PMTK604') # Firmware version query
#gps.exit_backup_mode()

# Key handling
key0 = Pin(15, Pin.IN, Pin.PULL_UP)
key1 = Pin(17, Pin.IN, Pin.PULL_UP)

screen = 0
screen_max = 1

# Variables to store button press times
key0_press_time = 0
key1_press_time = 0

# Threshold in milliseconds to distinguish between short and long press
LONG_PRESS_THRESHOLD = 1000  # 1 second

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
    print('Short Press UP')
    if screen >= screen_max:
        screen = 0
    else:
        screen += 1
    # Add your code to handle short press UP event

def handle_long_press_UP():
    global screen
    screen = 2
    print('Long Press UP')
    # Add your code to handle long press UP event

def handle_short_press_DN():
    global screen
    print('Short Press DN')
    if screen <= 0:
        screen = screen_max
    else:
        screen -= 1
    # Add your code to handle short press DN event

def handle_long_press_DN():
    global screen
    screen = 0
    print('Long Press DN')
    # Add your code to handle long press DN event

# Register the handler functions for both rising and falling edges
key0.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=button_UP_handler)
key1.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=button_DN_handler)

# GPS thread
def gps_thread():
    global received_sentence
    
    # GPS init
    UARTx = 0
    BAUDRATE = 9600
    gps=l76x.L76X(uartx=UARTx,_baudrate = BAUDRATE)
    #gps.exit_backup_mode()
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

#
# MAIN LOOP
#
OLED.init_display()
nmea_parser = NMEA.parser()
screen = 0
buffer = ''
while True:
    mutex.acquire()
    #print(buffer)
    #buffer = copy.copy(received_sentence)
    buffer = received_sentence
    if len(buffer) > 0:
        nmea_parser.parse_sentence(buffer)
    mutex.release()
        
    if screen == 0:
        OLED.fill(0)
        #OLED.hline(0,0,128,1)
        OLED.text(nmea_parser.get_time_string(), 30, 3, 1)
        OLED.hline(0,14,128,1)
        Writer.set_textpos(OLED, 17, 0)
        font_large.printstring(nmea_parser.get_lat_string())
        Writer.set_textpos(OLED, 32, 0)
        font_large.printstring(nmea_parser.get_lon_string())
        OLED.hline(0,48,128,1)
        OLED.text(nmea_parser.fix_type + ' ' + nmea_parser.mode + ' ' + str(nmea_parser.birds_in_use) + '/' + str(nmea_parser.birds_in_view), 0, 54, 1)
        OLED.show()
    elif screen == 1:
        OLED.fill(0)
        rcv = nmea_parser.sentences_received
        val = nmea_parser.sentences_valid
        inv = nmea_parser.sentences_invalid
        par = nmea_parser.sentences_parsed
        ign = nmea_parser.sentences_ignored
        OLED.text("rcv: " + str(nmea_parser.sentences_received), 0, 0*12, 1)
        OLED.text("val: " + str(round(val/rcv * 100)) + '% ' + nmea_parser.sentence_last_valid_type, 0, 1*12, 1)
        OLED.text("inv: " + str(round(inv/rcv * 100)) + '% ' + nmea_parser.sentence_last_invalid_type, 0, 2*12, 1)
        OLED.text("par: " + str(round(par/rcv * 100)) + '% ' + nmea_parser.sentence_last_parsed_type, 0, 3*12, 1)
        OLED.text("ign: " + str(round(ign/rcv * 100)) + '% ' + nmea_parser.sentence_last_ignored_type, 0, 4*12, 1)        
        OLED.show()
    elif screen == 2:
        OLED.fill(0)
        OLED.text(buffer, 0, 0, 1)
        OLED.text('Var:' + nmea_parser.magvar, 0, 15, 1)
        OLED.show()
    #print(f"Screen: {screen}")
    #utime.sleep_ms(10)



