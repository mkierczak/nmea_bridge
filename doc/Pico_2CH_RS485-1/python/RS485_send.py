'''
 ##@name：		RS485_send.py
 ##@auther:		waveshare team
 ##@info:		This code has configured a serial port of Pico to connect to our PICO-2CH-RS485, 
			which will continuously emit an incremental data.	
'''
from machine import UART, Pin
import time

#uart1 = UART(1, baudrate=115200, tx=Pin(4), rx=Pin(5))

uart0 = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1))
a=0
txData = b'RS485 send test...\r\n'
uart0.write(txData)
print('RS485 send test...')
time.sleep(0.1)
while True:
    a=a+1
    time.sleep(0.5) 
    print (a)#shell output
    uart0.write("{}\r\n".format(a))