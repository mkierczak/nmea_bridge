# -*- coding:utf-8 -*-

from machine import UART,Pin
import math
import utime

chars = '0123456789ABCDEF*'

class L76X(object):
    #FORCE_PIN  = None # 14
    #STANDBY_PIN = None # 17
    
    #Startup mode
    SET_HOT_START       = '$PMTK101'
    SET_WARM_START      = '$PMTK102'
    SET_COLD_START      = '$PMTK103'
    SET_FULL_COLD_START = '$PMTK104'

    #Standby mode -- Exit requires high level trigger
    SET_PERPETUAL_STANDBY_MODE      = '$PMTK161'
    SET_STANDBY_MODE                = '$PMTK161,0'

    SET_PERIODIC_MODE               = '$PMTK225'
    SET_NORMAL_MODE                 = '$PMTK225,0'
    SET_PERIODIC_BACKUP_MODE        = '$PMTK225,1,1000,2000'
    SET_PERIODIC_STANDBY_MODE       = '$PMTK225,2,1000,2000'
    SET_PERPETUAL_BACKUP_MODE       = '$PMTK225,4'
    SET_ALWAYSLOCATE_STANDBY_MODE   = '$PMTK225,8'
    SET_ALWAYSLOCATE_BACKUP_MODE    = '$PMTK225,9'

    #Set the message interval,100ms~10000ms
    SET_POS_FIX         = '$PMTK220'
    SET_POS_FIX_100MS   = '$PMTK220,100'
    SET_POS_FIX_200MS   = '$PMTK220,200'
    SET_POS_FIX_400MS   = '$PMTK220,400'
    SET_POS_FIX_800MS   = '$PMTK220,800'
    SET_POS_FIX_1S      = '$PMTK220,1000'
    SET_POS_FIX_2S      = '$PMTK220,2000'
    SET_POS_FIX_4S      = '$PMTK220,4000'
    SET_POS_FIX_8S      = '$PMTK220,8000'
    SET_POS_FIX_10S     = '$PMTK220,10000'

    #Switching time output
    SET_SYNC_PPS_NMEA_OFF   = '$PMTK255,0'
    SET_SYNC_PPS_NMEA_ON    = '$PMTK255,1'

    #To restore the system default setting
    SET_REDUCTION               = '$PMTK314,-1'
    #SET_NMEA_OUTPUT = '$PMTK314,1,1,1,1,1,1,1,1,0,0,0,0,0,0,0,0,0,1,0'
    SET_NMEA_OUTPUT = '$PMTK314,0,0,0,1,5,5,0,0,0,0,0,0,0,0,0,0,0,0,0'
    SET_GPS_SEARCH_MODE = '$PMTK353,1,0,0,0,0' # $PMTK353,GPS_Enable,0,0,0,BEIDOU_Enable

    #Baud rate
    SET_NMEA_BAUDRATE          = '$PMTK251'
    SET_NMEA_BAUDRATE_115200   = '$PMTK251,115200'
    SET_NMEA_BAUDRATE_57600    = '$PMTK251,57600'
    SET_NMEA_BAUDRATE_38400    = '$PMTK251,38400'
    SET_NMEA_BAUDRATE_19200    = '$PMTK251,19200'
    SET_NMEA_BAUDRATE_14400    = '$PMTK251,14400'
    SET_NMEA_BAUDRATE_9600     = '$PMTK251,9600'
    SET_NMEA_BAUDRATE_4800     = '$PMTK251,4800'
    
    _uart0 = 0
    _uart1 = 1
    
    def __init__(self,uartx=_uart0,_baudrate = 9600):
        if uartx==self._uart1:
            self.ser = UART(uartx,baudrate=_baudrate,tx=Pin(4), rx=Pin(5))
        else:
            self.ser = UART(uartx,baudrate=_baudrate,tx=Pin(0), rx=Pin(1))

        #self.StandBy = Pin(self.STANDBY_PIN,Pin.OUT)
        #self.Force = Pin(self.FORCE_PIN,Pin.IN)
        #self.StandBy.value(0)
        #self.Force.value(0)
    
    def send_command(self, data):
        Check = ord(data[1]) 
        for i in range(2, len(data)):
            Check = Check ^ ord(data[i]) 
        data = data + chars[16]
        data = data + chars[int(Check/16)]
        data = data + chars[int(Check%16)]
        self.uart_send_string(data.encode())
        self.uart_send_byte('\r'.encode())
        self.uart_send_byte('\n'.encode())
        utime.sleep(0.1)
        print(data)

    def set_baudrate(self, _baudrate, uartx=_uart0):
        if self._uart1==uartx:
            self.ser = UART(uartx,baudrate=_baudrate,tx=Pin(4),rx=Pin(5))
        else:
            self.ser = UART(uartx,baudrate=_baudrate,tx=Pin(0),rx=Pin(1))

    def set_nmea_output(self, f_GLL = 1, f_RMC = 1, f_VTG = 1, f_GGA = 1, f_GSA = 1, f_GSV = 1, f_ZDA = 1, f_MCHN = 0):
        """
        0 NMEA_SEN_GLL, // GPGLL interval - Geographic Position - Latitude longitude
        1 NMEA_SEN_RMC, // GPRMC interval - Recommended Minimum Specific GNSS Sentence
        2 NMEA_SEN_VTG, // GPVTG interval - Course over Ground and Ground Speed
        3 NMEA_SEN_GGA, // GPGGA interval - GPS Fix Data
        4 NMEA_SEN_GSA, // GPGSA interval - GNSS DOPS and Active Satellites
        5 NMEA_SEN_GSV, // GPGSV interval - GNSS Satellites in View
        6 - 16 //Reserved
        17 NMEA_SEN_ZDA, // GPZDA interval – Time & Date
        18 NMEA_SEN_MCHN, // PMTKCHN interval – GPS channel status
        0 - Disabled or not supported sentence
        N - Output once every N position fix N = [1, 2, 3, ,4, 5]
        
        $PMTKCHN is MTK3339 specific: compressed SVN channel info, 32 'ppnnt' values: pp-PRN, nn-SNR, t- 0 idle, 1 searching, 2 tracking
        The command can reset baudrate to 9600 according to internet!
        """
        # Validate that params are in the right range
        params = locals()
        for name, value in params.items():
            if value < 0 or value > 5:
                print(f"WARNNG! Invalid value of {name} = {value}. Values mus be within 0-5! Exiting!")
                exit()
        
        cmd = f"$PMTK314,{f_GLL},{f_RMC},{f_VTG},{f_GGA},{f_GSA},{f_GSV},0,0,0,0,0,0,0,0,0,0,0,{f_ZDA},{f_MCHN}"
        self.send_command(cmd)
                
    def exit_backup_mode(self):
        #self.Force.value(1)
        utime.sleep(1)
        #self.Force.value(0)
        utime.sleep(1)
        #self.Force = Pin(self.FORCE_PIN,Pin.IN)
        
    def uart_send_byte(self, value): 
        self.ser.write(value) 

    def uart_send_string(self, value): 
        self.ser.write(value)

    def uart_receive_byte(self): 
        return self.ser.read(1)

    def uart_receive_string(self, value): 
        data = self.ser.read(value)
        return data
    
    def uart_receive_line(self):
        data = self.ser.readline()
        return data
    
    def uart_any(self):
        return self.ser.any()
