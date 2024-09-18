# -*- coding:utf-8 -*-

from L76B import l76x

# define the UART number and its baudrate , when UARTx is 1 please solder the UART1 0R resistor on Pico-GPS-L76B
UARTx = 0
# define the rp2040 uart baudrate , the default baudrate is 9600 of L76B 
BAUDRATE = 9600

# make an object of gnss device , the default uart is UART0 and its baudrate is 9600bps
gnss_l76b=l76x.L76X(uartx=UARTx,_baudrate = BAUDRATE)

# exit the backup mode when start
gnss_l76b.L76X_Exit_BackupMode()
# enable sync PPS when NMEA output
gnss_l76b.L76X_Send_Command(gnss_l76b.SET_SYNC_PPS_NMEA_ON)

# loop
while True:
    # if gnss available,then print the nmea0183 sentence
    if gnss_l76b.Uart_Any():
        print(chr(gnss_l76b.Uart_ReceiveByte()[0]),end="")

