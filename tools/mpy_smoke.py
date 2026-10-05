"""Smoke test for real MicroPython (the unix port): imports the hardware-free modules and runs a short scenario,
so MicroPython-specific incompatibilities that CPython hides show up in CI.

    micropython tools/mpy_smoke.py        (run from the repository root)
"""
import sys

sys.path.insert(0, '.')

import NMEA
import bridge
import jamming
import linkcalc
import menu
import nav
import settings
import spoofing
import ui
import wificreds


def with_checksum(body):
    c = 0
    for ch in body:
        c ^= ord(ch)
    return '${}*{:02X}'.format(body, c)


class Radio(object):
    def __init__(self):
        self.written = []

    def write(self, text):
        self.written.append(text)


class Clock(object):
    now = 1000

    def __call__(self):
        return self.now


def check(condition, message):
    if not condition:
        raise AssertionError(message)


# parser
p = NMEA.Parser()
rmc = with_checksum('GNRMC,123519.000,A,4807.0380,N,01131.0000,E,5.00,90.00,040726,,,A')
check(p.parse_sentence(rmc, 100) and p.fix_count == 1, 'RMC not parsed')
check(p.lat_u == (48 * 60 + 7) * 10000 + 380, 'position units')
check(p.last_valid_sentence.startswith('$GPRMC') and p.last_valid_sentence.endswith('\r\n'), 'GN -> GP rewrite')
check(NMEA.valid_checksum(with_checksum('GPGGA,1,2')) and not NMEA.valid_checksum('$GPGGA,1,2*0'), 'checksum')
gsv = with_checksum('GPGSV,1,1,02,01,40,083,46,02,50,100,44')
p.parse_sentence(gsv, 101)
check(p.cn0_stats()[0] == 2 and p.sats_by_talker['GP'][0] == (1, 40, 46), 'GSV')

# the radio path
clock = Clock()
radio = Radio()
core = bridge.Bridge(NMEA.Parser(), bridge.RxQueue(8), radio, clock)
core.detector = jamming.JamDetector(core.parser)
core.spoof = spoofing.SpoofDetector(core.parser)
core.queue.push((clock.now, rmc))
core.queue.push((clock.now, with_checksum('BDGSV,1,1,00')))
core.step(clock.now)
check(len(radio.written) == 1 and radio.written[0].startswith('$GPRMC'), 'forwarding')
framer = bridge.SentenceFramer()
check(framer.feed(b'$GPGGA,1*00\r\n$GPRMC,2') == ['$GPGGA,1*00\r\n'], 'framer')
check(bridge.forward_decision('RMC', 'GN', ('RMC',), ('GN',), ('GN',), True, ('RMC',), True) == (False, False), 'block')

# settings, navigation, menu
check(settings.coerce('max_speed_kn', 63) == 65 and settings.next_value('gps_baud', 4800, -1) == 115200, 'settings')
tracker = nav.ButtonTracker('UP')
tracker.edge(0, 1000)
check(tracker.edge(1, 1200) == nav.UP_SHORT, 'button')
queue = nav.EventQueue(4)
queue.put('a')
check(queue.drain() == ['a'], 'event queue')
check(ui.REFRESH_MS > 0 and menu.ROWS == 4, 'ui/menu constants')

# credentials and link maths
check(wificreds.uid_suffix(bytes([1, 2, 3, 4, 5, 6, 7, 8])) == 'PW96', 'ssid suffix')
check(len(wificreds.generate_password(lambda n: bytes(range(n)))) == wificreds.PASSWORD_LENGTH, 'password')
check(linkcalc.nmea_burst_ms(4800, True) > 1000, 'link maths')

print('mpy smoke: OK')
