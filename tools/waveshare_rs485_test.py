from machine import UART, Pin
import utime

test_messages = [
b"$GPGGA,195953.520,6012.696,N,01743.041,E,1,12,1.0,0.0,M,0.0,M,,*63",
b"$GPGSA,A,3,01,02,03,04,05,06,07,08,09,10,11,12,1.0,1.0,1.0*30",
b"$GPRMC,195953.520,A,6012.696,N,01743.041,E,018.4,000.0,170424,000.0,W*7E"  
]

#uart0 = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1))
 # use uart 0
uart = UART(1, baudrate=4800, tx=Pin(4), rx=Pin(5))
 # use uart 1
#uart = machine.UART(1, baudrate=4800, tx=machine.Pin(8), rx=machine.Pin(9)) # custom uart on pins 11 and 12

uart.init(4800, bits=8, parity=None, stop=1)
utime.sleep(1) # grace time for UART to start

while True:
    for msg in test_messages:
        print(msg)
        uart.write(msg)
        utime.sleep(1)