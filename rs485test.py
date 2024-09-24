from machine import UART
import utime

test_messages2 = [
#"$GPGGA,195953.520,6012.696,N,01743.041,E,1,12,1.0,0.0,M,0.0,M,,*63\n",
#"$GPGSA,A,3,01,02,03,04,05,06,07,08,09,10,11,12,1.0,1.0,1.0*30\n",
"$GPRMC,195953.520,A,6012.696,N,01743.041,E,018.4,000.0,170424,000.0,W*7E\n"  
]

test_messages = [
"$GPRMC,194045.678,A,6013.2409,N,01744.1700,E,0.00,290.70,230924,,,A*63",
"$GNRMC,194045.678,A,6013.2409,N,01744.1700,E,0.00,290.70,230924,,,A*7D",
"$GNRMC,194045.678,A,6013.2409,N,01744.1700,E,0.00,290.70,230924,,,A*7D",
"$GNRMC,194045.678,A,6013.2409,N,01744.1700,E,0.00,290.70,230924,,,A*7D",
"$GPGGA,194045.678,6013.2409,N,01744.1700,E,1,6,1.25,53.7,M,23.6,M,,*7B\n",
"$GNRMC,194046.478,A,6013.2409,N,01744.1700,E,0.00,262.97,230924,,,A*78",
"$GNRMC,194046.478,A,6013.2409,N,01744.1700,E,0.00,262.97,230924,,,A*78"   
]
uart = machine.UART(1, baudrate=4800, tx=machine.Pin(4), rx=machine.Pin(5))
uart.init(4800, bits=8, parity=None, stop=1)
utime.sleep(1) # grace time for UART to start

while True:
    for msg in test_messages:
        print(msg)
        uart.write(msg)
        utime.sleep(1)