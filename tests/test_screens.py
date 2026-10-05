import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import NMEA
import nav
from jamming import JamDetector
from spoofing import SpoofDetector
from test_jamming import gsv, with_checksum
from test_menu import FakeOled


class FakeWriter:
    @staticmethod
    def set_textpos(oled, row, col):
        pass

    def __init__(self, *a, **k):
        pass

    def printstring(self, s):
        pass


class Oled(FakeOled):
    def __init__(self):
        super().__init__()
        self.rects = []

    def fill_rect(self, x, y, w, h, c):
        self.rects.append((x, y, w, h))

    def vline(self, *a):
        pass


_saved = sys.modules.get('writer')
stub = types.ModuleType('writer')
stub.Writer = FakeWriter
sys.modules['writer'] = stub
sys.modules.pop('screens', None)
import screens  # noqa: E402  (imported against the stub)
if _saved is None:
    sys.modules.pop('writer', None)
else:
    sys.modules['writer'] = _saved

STATS = {'rcvpm': 60, 'rcv': 10, 'val': 9, 'inv': 1, 'par': 8, 'ign': 1}
INFO = {'uptime_s': 3725, 'heap': 123456, 'dropped': 0, 'inv_pct': 3, 'baud': 4800, 'found': 9600,
        'fix_ms': 1000, 'gnss': 'GPS+BD', 'uid': 'e66164084371b26f', 'now_ms': 0}
WIFI = ('ON', 'NMEABridge-K7X2', '192.168.4.1', 2, 4, 'abcdefgh2345')


def populated_parser():
    p = NMEA.Parser()
    p.parse_sentence(with_checksum('GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,'))
    gsv(p, [48, 44, 41, 38, 35, 31, 28, 25], talker='GP')
    gsv(p, [43, 39, 33, 29], talker='BD')
    p.parse_sentence(with_checksum('PMTKSPF,2'))
    return p


def draw_all(parser, jam, spoof):
    drawn = []
    for page in nav.PAGES:
        oled = Oled()
        screens.draw(oled, FakeWriter(), page, parser, STATS, 0, False, jam, spoof, WIFI, INFO)
        drawn.append((page, oled))
    return drawn


def check_fits(drawn):
    for page, oled in drawn:
        for text, x, y in oled.calls:
            assert x + 8 * len(text) <= 128, (page, text, x)
            assert 0 <= y <= 57, (page, text, y)
        for x, y, w, h in oled.rects:
            assert 0 <= x and x + w <= 128 and 0 <= y and y + h <= 64


def test_all_pages_draw_with_detectors_off_and_empty_parser():
    drawn = draw_all(NMEA.Parser(), None, None)
    assert len(drawn) == len(nav.PAGES)
    check_fits(drawn)


def test_all_pages_draw_with_detectors_and_data():
    p = populated_parser()
    jam, spoof = JamDetector(p), SpoofDetector(p)
    jam.reason = 'CNM'
    drawn = draw_all(p, jam, spoof)
    check_fits(drawn)
    pages = dict(drawn)
    sats = pages[nav.PAGE_SATS]
    assert any(t.startswith('G') for t in sats.texts()) and any(t.startswith('B') for t in sats.texts())
    assert len(sats.rects) == 5                         # five strongest satellites get a bar
    signal = ' '.join(pages[nav.PAGE_SIGNAL].texts())
    assert 'cn0 sats module' in signal and 'mod:warn' in signal


def test_sats_page_sorted_strongest_first_and_caps_at_five():
    p = populated_parser()
    rows = screens._tracked(p)
    assert [r[0] for r in rows] == sorted((r[0] for r in rows), reverse=True)
    assert len(rows) == 12
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_SATS, p, STATS, 0, False, None, None, WIFI, INFO)
    assert oled.calls[0][0] == 'G8 B4  el  dB'


def test_spoof_page_shows_indicator_names_and_latch():
    p = NMEA.Parser()
    spoof = SpoofDetector(p)
    spoof.warm_fixes = 100
    spoof._events = {'K1': 0, 'T1': 0, 'S1': 0, 'K3': 0}
    spoof._alert_at = 0
    spoof.state = 'ALERT'
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_SPOOF, p, STATS, 0, False, None, spoof, WIFI,
                 dict(INFO, now_ms=60000))
    texts = oled.texts()
    assert texts[0] == 'SPF ALERT' and 'K1 jump' in texts and 'T1 time' in texts
    assert '+1 more' in texts and any(t.startswith('latch 9:') for t in texts)


def test_system_page_content():
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_SYSTEM, NMEA.Parser(), STATS, 0, False, None, None,
                 WIFI, INFO)
    assert oled.texts() == ['up 1h02m05s', 'heap 120k free', 'drop 0 inv 3%', 'b4800<9600',
                            'fix1000ms GPS+BD', 'e66164084371b26f']


def test_wifi_page_shows_credentials():
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_WIFI, NMEA.Parser(), STATS, 0, False, None, None, WIFI, INFO)
    assert 'NMEABridge-K7X2' in oled.texts() and 'PW abcdefgh2345' in oled.texts()


def test_baud_line_always_fits():
    assert screens._baud_line(4800, None) == 'baud 4800 ?'
    assert screens._baud_line(4800, 4800) == 'baud 4800 ok'
    assert screens._baud_line(115200, 115200) == 'baud 115200 ok'
    assert len(screens._baud_line(115200, 57600)) <= 16 and len(screens._baud_line(115200, None)) <= 16
