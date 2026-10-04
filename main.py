from machine import Pin, SPI, WDT, UART
from writer import Writer
import gc
import utime
import _thread

import l76x
import NMEA
import screens
import wifipass
from jamming import JamDetector
from spoofing import SpoofDetector, TIME_JUMP_MS
import sh1107
import roboto14

# Variables
DEBUG = True                      # echo forwarded sentences to the REPL
SCREEN_REFRESH_RATE = 0.5 * 1000  # how often to refresh screen
STATS_REFRESH_RATE = 10 * 1000    # how often (in milliseconds) stats will be refreshed
STATS_MULTIPLIER = 60 * 1000 / STATS_REFRESH_RATE # multiplier to get stats per minute
LONG_PRESS_THRESHOLD = 1 * 1000   # threshold in milliseconds to distinguish between short and long press
WATCHDOG_TIMEOUT = 5 * 1000       # watchdog has to be fed every N milliseconds
WIFI_TOGGLE_PRESS = 3 * 1000      # hold UP this long (ms) to switch the Wi-Fi access point on/off
GPS_SILENCE_TIMEOUT = 30 * 1000   # stop feeding watchdog (=> reset) if no GPS data for N milliseconds
FORWARD_TYPES = ('RMC', 'GGA', 'GSA', 'GSV', 'ZDA')  # sentence types forwarded to the radio
FIX_STALE_TIMEOUT = 10 * 1000     # show NO FIX if no position update for N milliseconds
JAM_DETECT = True                 # signal-degradation / jamming indicator (see jamming.py)
JAM_EVAL_PERIOD = 2 * 1000        # how often the detector looks for a new GSV cycle
SPOOF_DETECT = True               # spoofing-suspicion indicator (see spoofing.py)
SPOOF_ACTION = 'display'          # 'display': only show SPF?/SPF!; 'block': also stop forwarding SPOOF_BLOCK_TYPES during an ALERT
SPOOF_BLOCK_TYPES = ('RMC', 'GGA')
FORWARD_TALKERS = ('GP', 'GN')    # talker IDs forwarded to the radio (BeiDou $BDGSV/$BDGSA are not)
GNSS_MODE = 'GPS+BD'              # 'GPS' or 'GPS+BD' (L76B supports no other constellations)
WIFI_ENABLE = True                # Pico W only: allow the NMEA-over-Wi-Fi access point (off at boot; hold UP to toggle)
WIFI_SSID = 'nmea-bridge'
WIFI_PASSWORD = ''                # '' = random password made on first boot, stored in wifi_password.txt and shown on the Wi-Fi screen; or set your own (8+ characters)
WIFI_PORT = 10110                 # NMEA 0183 over TCP (server) and UDP (broadcast)
WIFI_MAX_CLIENTS = 4
WIFI_FORWARD_TYPES = FORWARD_TYPES  # sentence types sent to Wi-Fi clients
WIFI_FORWARD_TALKERS = ('GP', 'GN', 'BD')
MEM_REPORT_PERIOD = 60 * 1000     # print free/used heap this often when DEBUG is on
UARTx = 0                         # GPS UART
GPS_BAUDRATE = 4800               # GPS link rate: 4800, 9600, 14400, 19200, 38400, 57600 or 115200 (module is switched at boot)
FIX_INTERVAL_MS = 1000 if GPS_BAUDRATE <= 4800 else 800  # slow links need a longer fix interval

# Pins and buses
PIN_OLED_SCK, PIN_OLED_MOSI = 10, 11
PIN_OLED_DC, PIN_OLED_RST, PIN_OLED_CS = 8, 12, 9
OLED_SPI_BAUDRATE = 10_000_000
PIN_KEY_UP, PIN_KEY_DN = 15, 17
RS485_UART = 1                    # must differ from UARTx when both use UART1 pins
RS485_TX, RS485_RX = 4, 5
RS485_BAUDRATE = 4800             # what the VHF radio expects
RS485_TXBUF = 512                 # TX buffer so uart.write doesn't block the main loop

# Display
spi1 = SPI(1, baudrate=OLED_SPI_BAUDRATE, sck=Pin(PIN_OLED_SCK), mosi=Pin(PIN_OLED_MOSI), miso=None)
oled = sh1107.SH1107_SPI(128, 64, spi1, Pin(PIN_OLED_DC), Pin(PIN_OLED_RST), Pin(PIN_OLED_CS), rotate=180)
font_large = Writer(oled, roboto14)
oled.init_display()
oled.fill(0)
oled.text('Waiting for fix...', 0, 0, 1)
oled.show()

# Key handling
key0 = Pin(PIN_KEY_UP, Pin.IN, Pin.PULL_UP)
key1 = Pin(PIN_KEY_DN, Pin.IN, Pin.PULL_UP)

SCREEN_MAIN, SCREEN_STATS, SCREEN_DEBUG, SCREEN_WIFI = (
    screens.SCREEN_MAIN, screens.SCREEN_STATS, screens.SCREEN_DEBUG, screens.SCREEN_WIFI)
screen = SCREEN_MAIN
# short presses cycle through these; the debug screen is long-press only
SCREEN_CYCLE = (SCREEN_MAIN, SCREEN_STATS, SCREEN_WIFI) if WIFI_ENABLE else (SCREEN_MAIN, SCREEN_STATS)

press_time = {PIN_KEY_UP: 0, PIN_KEY_DN: 0}
wifi_toggle = False               # set from the button IRQ; the main loop does the actual work

def step_screen(direction):
    global screen
    if screen in SCREEN_CYCLE:
        screen = SCREEN_CYCLE[(SCREEN_CYCLE.index(screen) + direction) % len(SCREEN_CYCLE)]
    else:
        screen = SCREEN_MAIN

def make_button_handler(key_id, direction, long_target):
    def handler(pin):
        global screen, wifi_toggle
        if pin.value() == 0:  # pressed (active low)
            press_time[key_id] = utime.ticks_ms()
        else:                 # released
            held = utime.ticks_diff(utime.ticks_ms(), press_time[key_id])
            if WIFI_ENABLE and key_id == PIN_KEY_UP and held >= WIFI_TOGGLE_PRESS:
                wifi_toggle = True  # no network calls in IRQ context
            elif held >= LONG_PRESS_THRESHOLD:
                screen = long_target
            else:
                step_screen(direction)
    return handler

# Register the handler functions for both rising and falling edges
key0.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=make_button_handler(PIN_KEY_UP, +1, SCREEN_DEBUG))
key1.irq(trigger=Pin.IRQ_FALLING | Pin.IRQ_RISING, handler=make_button_handler(PIN_KEY_DN, -1, SCREEN_MAIN))

# Threading
rx_queue = []                     # complete sentences waiting for the main loop
rx_dropped = 0                    # sentences dropped because the queue was full
RX_QUEUE_MAX = 16                 # drop oldest beyond this
MAX_SENTENCE_LEN = 100            # longer buffers are garbage; resync on next '$'
mutex = _thread.allocate_lock()

def publish(buffer, rx_ms):
    global rx_dropped
    sentence = buffer.decode()
    mutex.acquire()
    if len(rx_queue) >= RX_QUEUE_MAX:
        rx_queue.pop(0)
        rx_dropped += 1
    rx_queue.append((rx_ms, sentence))
    mutex.release()

def gps_thread():

    # GPS init
    gps = l76x.L76X(uartx=UARTx, _baudrate=GPS_BAUDRATE, verbose=DEBUG)
    found = gps.configure_baudrate(GPS_BAUDRATE)  # find the module's current rate, switch it if needed
    avg_load, worst_load = l76x.nmea_load(GPS_BAUDRATE, FIX_INTERVAL_MS, GNSS_MODE == 'GPS+BD')
    if DEBUG:
        print('GPS baud {}: module found at {}'.format(GPS_BAUDRATE, found))
        print('GPS link load: avg {:.0f}%, worst cycle {:.0f}%'.format(avg_load * 100, worst_load * 100))
        if avg_load > 0.8 or worst_load > 1.0:
            print('WARNING: GPS link may be overloaded; raise GPS_BAUDRATE or use GNSS_MODE = GPS')
    gps.send_command(gps.PMTK_API_SET_STOP_QZSS) # disable Japanese QZSS
    gps.send_command(gps.PMTK_API_SET_SBAS_ENABLED)
    gps.send_command(gps.PMTK_API_SET_DGPS_MODE)
    gps.send_command(gps.PMTK_ENABLE_EASY) 
    gps.send_command(l76x.fix_interval_command(FIX_INTERVAL_MS))
    gps.send_command(gps.SET_NORMAL_MODE)
    gps.send_command(gps.SET_GPS_BEIDOU_SEARCH_MODE if GNSS_MODE == 'GPS+BD' else gps.SET_GPS_SEARCH_MODE)
    gps.send_command(gps.SET_SYNC_PPS_NMEA_ON)
    gps.send_command(gps.SET_NMEA_OUTPUT)
    gps.send_command(gps.PMTK_SET_AIC)  # acks ($PMTK001,<cmd>,3) are parsed by NMEA.Parser
    gps.send_command(gps.PMTK_JAM_DETECT_ON)  # module jamming detector -> $PMTKSPF
    
    buffer = bytearray()
    while True:
        n = gps.uart_any()
        if not n:
            utime.sleep_ms(2)
            continue
        try:
            data = gps.uart_receive_string(n)
        except OSError:
            continue
        if not data:
            continue
        for ascii_char in data:
            if not 10 <= ascii_char <= 126:
                continue
            if ascii_char == 0x24:  # '$' - beginning of a new sentence
                if buffer:
                    publish(buffer, utime.ticks_ms())
                buffer = bytearray()
            if len(buffer) < MAX_SENTENCE_LEN:
                buffer.append(ascii_char)
            if ascii_char == 10 and buffer:  # end of line: publish right away (accurate arrival time)
                publish(buffer, utime.ticks_ms())
                buffer = bytearray()

_thread.start_new_thread(gps_thread, ())

def gather_stats():
    stats = nmea_parser.snapshot_and_reset()
    stats['rcvpm'] = stats['rcv'] * STATS_MULTIPLIER # received per minute
    return stats

broadcaster = None                # wifi.NmeaBroadcaster, created the first time Wi-Fi is switched on
wifi_password = ''
if WIFI_ENABLE:
    wifi_password, wifi_pw_stored = wifipass.get_password(WIFI_PASSWORD)
    if DEBUG and not wifi_pw_stored:
        print('WARNING: could not store the Wi-Fi password; it changes at every boot')

def set_wifi(on):
    """Start/stop the access point. wifi.py is imported lazily so the default footprint stays small."""
    global broadcaster
    if on:
        if broadcaster is None:
            import network
            import socket
            import wifi
            broadcaster = wifi.NmeaBroadcaster(network, socket, WIFI_SSID, wifi_password,
                                               WIFI_PORT, WIFI_MAX_CLIENTS)
        gc.collect()
        try:
            broadcaster.start(feed=wdt.feed if wdt else None, sleep_ms=utime.sleep_ms)
        except Exception as e:  # bad password, AP did not come up, ...
            print('Wi-Fi start failed:', e)
        gc.collect()
    elif broadcaster:
        broadcaster.stop()
        gc.collect()

def wifi_info():
    state, ssid, ip, clients, max_clients = (broadcaster.info() if broadcaster
                                             else ('OFF', WIFI_SSID, '', 0, WIFI_MAX_CLIENTS))
    return state, ssid, ip, clients, max_clients, wifi_password

def memreport(tag):
    gc.collect()
    print('MEM {}: free {} B, used {} B'.format(tag, gc.mem_free(), gc.mem_alloc()))

#
# MAIN LOOP
#
nmea_parser = NMEA.Parser()
stats = { 'rcvpm' : 1, 'rcv' : 1, 'val' : 0, 'inv' : 0, 'par' : 0, 'ign' : 0 }
last_display_update = utime.ticks_ms()

# RS-485 setup
uart = UART(RS485_UART, baudrate=RS485_BAUDRATE, tx=Pin(RS485_TX), rx=Pin(RS485_RX), txbuf=RS485_TXBUF)
uart.init(RS485_BAUDRATE, bits=8, parity=None, stop=1)
utime.sleep(1) # grace time for UART to start

# Watchdog
last_stats = utime.ticks_ms()
last_gps_rx = utime.ticks_ms()
last_pos = None                   # ticks of last valid GGA/RMC
wdt = None                        # armed on first GPS sentence (can't be stopped once started)
last_sig = None
detector = JamDetector(nmea_parser) if JAM_DETECT else None
last_jam_eval = utime.ticks_ms()
# GPS time check must tolerate how late a sentence can arrive behind a big GSA/GSV cycle
spoof = (SpoofDetector(nmea_parser, TIME_JUMP_MS + l76x.nmea_burst_ms(GPS_BAUDRATE, GNSS_MODE == 'GPS+BD'))
         if SPOOF_DETECT else None)
last_spoof_eval = utime.ticks_ms()
logged_acks = {}
wifi_lines = []                   # sentences collected for one batched Wi-Fi send
last_wifi_poll = utime.ticks_ms()
last_mem_report = utime.ticks_ms()
if DEBUG:
    memreport('boot')

# Main loop
while True:
    # Feed watchdog only while GPS data keeps arriving
    if wdt and utime.ticks_diff(utime.ticks_ms(), last_gps_rx) < GPS_SILENCE_TIMEOUT:
        wdt.feed()

    # Take pending sentences under the lock, parse outside it
    mutex.acquire()
    pending = rx_queue[:]
    del rx_queue[:]
    mutex.release()
    if pending:
        last_gps_rx = utime.ticks_ms()
        if wdt is None:
            wdt = WDT(timeout=WATCHDOG_TIMEOUT)

    for rx_ms, buffer in pending:
        if nmea_parser.parse_sentence(buffer, rx_ms):
            sentence_type = nmea_parser.sentence_last_valid_type
            if sentence_type in ('GGA', 'RMC'):
                last_pos = utime.ticks_ms()
            if spoof and sentence_type in ('RMC', 'GGA', 'GSV'):
                spoof.evaluate(utime.ticks_ms())
            if (SPOOF_ACTION == 'block' and spoof and spoof.state == 'ALERT' and
                    sentence_type in SPOOF_BLOCK_TYPES):
                continue  # possible spoofing: don't hand this position to the radio
            talker = buffer[1:3]
            if sentence_type in FORWARD_TYPES and talker in FORWARD_TALKERS:
                # forward the (fixed) sentence to the VHF radio, once
                uart.write(nmea_parser.last_valid_sentence)
                if DEBUG:
                    print(nmea_parser.last_valid_sentence.strip())
            if (broadcaster and broadcaster.active and sentence_type in WIFI_FORWARD_TYPES
                    and talker in WIFI_FORWARD_TALKERS):
                wifi_lines.append(nmea_parser.last_valid_sentence)

    # Wi-Fi: toggle request from the button, one batched send per loop, accept new clients
    if wifi_toggle:
        wifi_toggle = False
        set_wifi(not (broadcaster and broadcaster.active))
        last_sig = None  # force a redraw
    if broadcaster and broadcaster.active:
        if wifi_lines:
            broadcaster.send(''.join(wifi_lines))
        if utime.ticks_diff(utime.ticks_ms(), last_wifi_poll) > 200:
            broadcaster.poll()
            last_wifi_poll = utime.ticks_ms()
    del wifi_lines[:]

    # Gather stats every STATS_REFRESH_RATE milliseconds
    if utime.ticks_diff(utime.ticks_ms(), last_stats) > STATS_REFRESH_RATE:
        stats = gather_stats()
        last_stats = utime.ticks_ms()

    no_fix = (nmea_parser.fix_type == 'NO' or last_pos is None or
              utime.ticks_diff(utime.ticks_ms(), last_pos) > FIX_STALE_TIMEOUT)

    # Expire spoofing indicators even when no new data arrives
    if spoof and utime.ticks_diff(utime.ticks_ms(), last_spoof_eval) > 1000:
        spoof.evaluate(utime.ticks_ms())
        last_spoof_eval = utime.ticks_ms()

    # Report module replies to our configuration commands (3 = success)
    if DEBUG:
        for cmd in (251, 286, 353, 838):
            if cmd in nmea_parser.pmtk_acks and logged_acks.get(cmd) != nmea_parser.pmtk_acks[cmd]:
                logged_acks[cmd] = nmea_parser.pmtk_acks[cmd]
                print('PMTK{} ack: {}'.format(cmd, logged_acks[cmd]))

    # Look for signal degradation once per JAM_EVAL_PERIOD (acts on new GSV cycles only)
    if detector and utime.ticks_diff(utime.ticks_ms(), last_jam_eval) > JAM_EVAL_PERIOD:
        detector.evaluate(utime.ticks_ms(), not no_fix)
        last_jam_eval = utime.ticks_ms()

    if DEBUG and utime.ticks_diff(utime.ticks_ms(), last_mem_report) > MEM_REPORT_PERIOD:
        memreport('run')
        last_mem_report = utime.ticks_ms()

    # Update the OLED only every N ms, and only if something visible changed
    if utime.ticks_diff(utime.ticks_ms(), last_display_update) > SCREEN_REFRESH_RATE:
        sig = (screen, no_fix, rx_dropped, tuple(stats.values()), nmea_parser.display_signature(),
               detector.signature() if detector else None, spoof.signature() if spoof else None,
               wifi_info() if screen == SCREEN_WIFI else None)
        if sig != last_sig:
            screens.draw(oled, font_large, screen, nmea_parser, stats, rx_dropped, no_fix, detector, spoof, wifi_info())
            oled.show()
            last_sig = sig
        last_display_update = utime.ticks_ms()

    utime.sleep(0.01) # yield so the GPS thread isn't starved
