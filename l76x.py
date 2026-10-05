# -*- coding:utf-8 -*-

from machine import UART,Pin
import utime

import NMEA

chars = '0123456789ABCDEF*'

ALLOWED_BAUDRATES = (4800, 9600, 14400, 19200, 38400, 57600, 115200)
_PROBE_ORDER = (9600, 4800, 115200, 57600, 38400, 19200, 14400)

from linkcalc import nmea_load, nmea_burst_ms  # noqa: F401  (pure helpers, re-exported for callers)


def baud_command(rate):
    if rate not in ALLOWED_BAUDRATES:
        raise ValueError('unsupported GPS baudrate {}'.format(rate))
    return '$PMTK251,{}'.format(rate)


def fix_interval_command(ms):
    if not 100 <= ms <= 10000:
        raise ValueError('fix interval must be 100..10000 ms')
    return '$PMTK220,{}'.format(ms)


class L76X(object):
    # Startup mode
    SET_HOT_START       = '$PMTK101'
    SET_WARM_START      = '$PMTK102'
    SET_COLD_START      = '$PMTK103'
    SET_FULL_COLD_START = '$PMTK104'

    # Standby mode -- Exit requires high level trigger
    SET_PERPETUAL_STANDBY_MODE      = '$PMTK161'
    SET_STANDBY_MODE                = '$PMTK161,0'

    SET_PERIODIC_MODE               = '$PMTK225'
    SET_NORMAL_MODE                 = '$PMTK225,0'
    SET_PERIODIC_BACKUP_MODE        = '$PMTK225,1,1000,2000'
    SET_PERIODIC_STANDBY_MODE       = '$PMTK225,2,1000,2000'
    SET_PERPETUAL_BACKUP_MODE       = '$PMTK225,4'
    SET_ALWAYSLOCATE_STANDBY_MODE   = '$PMTK225,8'
    SET_ALWAYSLOCATE_BACKUP_MODE    = '$PMTK225,9'

    # Set the message interval,100ms~10000ms
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

    # Switching time output
    SET_SYNC_PPS_NMEA_OFF   = '$PMTK255,0'
    SET_SYNC_PPS_NMEA_ON    = '$PMTK255,1'

    # To restore the system default setting
    SET_REDUCTION               = '$PMTK314,-1'
    SET_NMEA_OUTPUT = '$PMTK314,0,1,0,1,5,5,0,0,0,0,0,0,0,0,0,0,0,1,0' # RMC, GGA, GSV, GSA, ZDA
    # $PMTK353,GPS,0,0,0,BeiDou (L76-LB: GPS and GPS+BeiDou only; the 3 middle fields are reserved)
    SET_GPS_SEARCH_MODE = '$PMTK353,1,0,0,0,0'          # GPS only
    SET_GPS_BEIDOU_SEARCH_MODE = '$PMTK353,1,0,0,0,1'   # GPS + BeiDou

    # Baud rate
    SET_NMEA_BAUDRATE          = '$PMTK251'
    SET_NMEA_BAUDRATE_115200   = '$PMTK251,115200'
    SET_NMEA_BAUDRATE_57600    = '$PMTK251,57600'
    SET_NMEA_BAUDRATE_38400    = '$PMTK251,38400'
    SET_NMEA_BAUDRATE_19200    = '$PMTK251,19200'
    SET_NMEA_BAUDRATE_14400    = '$PMTK251,14400'
    SET_NMEA_BAUDRATE_9600     = '$PMTK251,9600'
    SET_NMEA_BAUDRATE_4800     = '$PMTK251,4800'
    
    # Augmentation
    PMTK_API_SET_SBAS_ENABLED   = '$PMTK313,1'
    PMTK_API_SET_DGPS_MODE      = '$PMTK301,2'
    PMTK_ENABLE_EASY            = '$PMTK869,1,1'
    PMTK_JAM_DETECT_ON          = '$PMTK838,1'  # module jamming detector; reports $PMTKSPF,1|2|3
    PMTK_SET_AIC                = '$PMTK286,1'  # Active Interference Cancellation; ack is $PMTK001,286,3
    
    # Other
    PMTK_API_SET_STOP_QZSS = '$PMTK352,0' # disable Japanese QZSS
    
    _uart0 = 0
    _uart1 = 1
    
    # Default pins per UART (Pico): UART0 -> GP0/GP1, UART1 -> GP4/GP5
    _default_pins = {0: (0, 1), 1: (4, 5)}

    RX_BUFFER = 1024  # bytes; rides out main-loop stalls at 9600 baud

    def __init__(self, uartx=_uart0, _baudrate=9600, tx=None, rx=None, verbose=False):
        self.verbose = verbose
        self.baudrate = _baudrate
        self._open(uartx, _baudrate, tx, rx)

    def _open(self, uartx, baudrate, tx=None, rx=None):
        args = (uartx, tx, rx)
        ser = getattr(self, 'ser', None)
        if ser is not None and args == getattr(self, '_uart_args', None):
            try:                          # probing rates: change the baud rate in place, keep the buffers
                ser.init(baudrate=baudrate)
                self.baudrate = baudrate
                return
            except (AttributeError, TypeError, ValueError, OSError):
                pass                      # not supported by this build: create a fresh UART below
        self._uart_args = args
        self.baudrate = baudrate
        d_tx, d_rx = self._default_pins[uartx]
        self.ser = UART(uartx, baudrate=baudrate,
                        tx=Pin(d_tx if tx is None else tx),
                        rx=Pin(d_rx if rx is None else rx),
                        rxbuf=self.RX_BUFFER)
    
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
        if self.verbose:
            print(data)

    def set_baudrate(self, _baudrate, uartx=_uart0, tx=None, rx=None):
        self._open(uartx, _baudrate, tx, rx)

    def _listen(self, listen_ms):
        """True if a complete NMEA sentence with a valid checksum arrives within listen_ms."""
        buf = bytearray()
        start = utime.ticks_ms()
        while utime.ticks_diff(utime.ticks_ms(), start) < listen_ms:
            n = self.ser.any()
            if not n:
                utime.sleep_ms(5)
                continue
            data = self.ser.read(n)
            if data:
                buf.extend(data)
            parts = bytes(buf).split(b'\n')
            rest = parts[-1]
            for line in parts[:-1]:
                text = ''.join(chr(b) for b in line if 32 <= b < 127)
                if text.startswith('$') and NMEA.valid_checksum(text):
                    return True
            buf = bytearray(rest[-200:])  # keep the unfinished line (bounded)
        return False

    def configure_baudrate(self, target, listen_ms=1500):
        """Make the module talk at 'target' baud and leave our UART at that rate.

        Listens at the target first (module already configured: nothing is sent). Otherwise probes
        the other rates, tells the module to switch with $PMTK251 and verifies. Returns the rate the
        module was found at, or None if it was never heard (UART is then left at the target)."""
        command = baud_command(target)  # validates the rate
        uartx, tx, rx = self._uart_args
        self._open(uartx, target, tx, rx)
        if self._listen(listen_ms):
            return target
        for rate in _PROBE_ORDER:
            if rate == target:
                continue
            self._open(uartx, rate, tx, rx)
            if not self._listen(listen_ms):
                continue
            self.send_command(command)
            utime.sleep(0.3)
            self._open(uartx, target, tx, rx)
            if self._listen(listen_ms):
                return rate
            self._open(uartx, rate, tx, rx)  # switch did not take effect: keep talking at the old rate
        self._open(uartx, target, tx, rx)
        return None

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
        Values:
        0 - Disabled or not supported sentence
        N - Output once every N position fix N = [1, 2, 3, ,4, 5]
        
        $PMTKCHN is MTK3339 specific: compressed SVN channel info, 32 'ppnnt' values: pp-PRN, nn-SNR, t- 0 idle, 1 searching, 2 tracking
        The command can reset baudrate to 9600 according to internet!
        """
        # Validate that params are in the right range
        params = {'f_GLL': f_GLL, 'f_RMC': f_RMC, 'f_VTG': f_VTG, 'f_GGA': f_GGA,
                  'f_GSA': f_GSA, 'f_GSV': f_GSV, 'f_ZDA': f_ZDA, 'f_MCHN': f_MCHN}
        for name, value in params.items():
            if value < 0 or value > 5:
                raise ValueError("Invalid value of {} = {}. Values must be within 0-5".format(name, value))
        
        cmd = f"$PMTK314,{f_GLL},{f_RMC},{f_VTG},{f_GGA},{f_GSA},{f_GSV},0,0,0,0,0,0,0,0,0,0,0,{f_ZDA},{f_MCHN}"
        self.send_command(cmd)
                
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
