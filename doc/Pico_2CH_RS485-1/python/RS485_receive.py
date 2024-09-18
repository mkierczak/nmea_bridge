'''
 ##@name：		RS485_receive.py
 ##@auther:		waveshare team
 ##@info:		This code configues a serial port of the Pico to connect to our PICO-2CH-RS485,
			and when the serial port receives any data it will return to them.
'''
from machine import UART, Pin
import time

#uart1 = UART(1, baudrate=115200, tx=Pin(4), rx=Pin(5))

uart1 = UART(0, baudrate=115200, tx=Pin(0), rx=Pin(1))
flag = 1
txData = 'RS485 receive test...\r\n'
uart1.write(txData)
print('RS485 receive test...')
time.sleep(0.1)
while True:
    rxData = bytes()
    while uart1.any() > 0:
        rxData = uart1.read()
        if(flag == 1):
            time.sleep(0.05)
            flag=0
        uart1.write("{}".format(rxData.decode('utf-8')))
        if(uart1.any()==0):
            uart1.write("\r\n")
            flag=1
