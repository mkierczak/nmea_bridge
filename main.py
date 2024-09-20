from machine import Pin, SPI
from writer import Writer
import utime
import _thread

import l76x
import NMEA
import sh1107
import freesans20 #dogica_gps

# Vriables
DEBUG = True

# Threading
received_sentence = ''
mutex = _thread.allocate_lock()

# Display
spi1 = SPI(1, baudrate=1_000_000, sck=Pin(10), mosi=Pin(11), miso=None) # SCK MOSI MISO
OLED = sh1107.SH1107_SPI(128, 64, spi1, Pin(8), Pin(12), Pin(9), rotate=180) # DC RST CS
dogica = Writer(OLED, freesans20)
OLED.init_display()
OLED.fill(0)
OLED.text('Waiting for GPS...', 0, 0, 1)
OLED.show()

#gps.set_baudrate(BAUDRATE)
#gps.send_command('$PMTK604') # Firmware version query
#gps.exit_backup_mode()

# Key handling
keyA = Pin(15, Pin.IN, Pin.PULL_UP)
keyB = Pin(17, Pin.IN, Pin.PULL_UP)
screen_max = 2
screen = 0
last_time = 0

def button_UP_pressed_handler(pin):
    global screen, last_time
    new_time = utime.ticks_ms()
    # if it has been more that 1/5 of a second since the last event, we have a new event
    if (new_time - last_time) > 200:
        if screen == screen_max:
            screen = 0
        else:
            screen = screen + 1
        last_time = new_time

def button_DN_pressed_handler(pin):
    global screen, last_time
    new_time = utime.ticks_ms()
    # if it has been more that 1/5 of a second since the last event, we have a new event
    if (new_time - last_time) > 200:
        if screen == 0:
            screen = screen_max
        else:
            screen = screen - 1
        last_time = new_time

# now we register the handler function when the button is pressed
keyA.irq(trigger=machine.Pin.IRQ_FALLING, handler = button_UP_pressed_handler)
keyB.irq(trigger=machine.Pin.IRQ_FALLING, handler = button_DN_pressed_handler)

# GPS thread
def gps_thread():
    global received_sentence
    
    # GPS init
    UARTx = 0
    BAUDRATE = 9600
    gps=l76x.L76X(uartx=UARTx,_baudrate = BAUDRATE)
    gps.exit_backup_mode()
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

buffer = ''
while True:
    mutex.acquire()
    if len(received_sentence) > 0:
        buffer = received_sentence
        nmea_parser.parse_sentence(buffer)
        received_sentence = ''
    mutex.release()    
    
    if screen == 0:
        OLED.fill(0)
        Writer.set_textpos(OLED, 0, 25)
        dogica.printstring(nmea_parser.get_time_string())
        Writer.set_textpos(OLED, 20, 0)
        dogica.printstring(nmea_parser.get_lat_string())
        Writer.set_textpos(OLED, 40, 0)
        dogica.printstring(nmea_parser.get_lon_string())
        OLED.show()
    elif screen == 1:
        OLED.fill(0)
        OLED.text("rcv: " + str(nmea_parser.sentences_received), 0, 0*12, 1)
        OLED.text("val: " + str(nmea_parser.sentences_valid) + ' | ' + nmea_parser.sentence_last_valid_type, 0, 1*12, 1)
        OLED.text("inv: " + str(nmea_parser.sentences_invalid) + ' | ' + nmea_parser.sentence_last_invalid_type, 0, 2*12, 1)
        OLED.text("par: " + str(nmea_parser.sentences_parsed) + ' | ' + nmea_parser.sentence_last_parsed_type, 0, 3*12, 1)
        OLED.text("ign: " + str(nmea_parser.sentences_ignored) + ' | ' + nmea_parser.sentence_last_ignored_type, 0, 4*12, 1)        
        OLED.show()
    elif screen == 2:
        OLED.fill(0)
        OLED.text(buffer, 0, 0, 1)
        OLED.show()
    #print(f"Screen: {screen}")
    #utime.sleep_ms(10)



