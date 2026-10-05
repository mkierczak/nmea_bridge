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
        'fix_ms': 1000, 'gnss': 'GPS+BD', 'uid': 'e66164084371b26f', 'now_ms': 0, 'version': '4000fde'}
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
    """Every text inside the 128x64 display (8x8 glyphs) and no two texts sharing pixels."""
    for page, oled in drawn:
        for text, x, y in oled.calls:
            assert x + 8 * len(text) <= 128, (page, text, x)
            assert 0 <= y <= 56, (page, text, y)
        for i, (t1, x1, y1) in enumerate(oled.calls):
            for t2, x2, y2 in oled.calls[i + 1:]:
                if not (x1 + 8 * len(t1) <= x2 or x2 + 8 * len(t2) <= x1):   # they share columns
                    assert abs(y1 - y2) >= 9, (page, t1, t2)                   # so a pixel of gap between rows
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
    assert 'cn0 sat mod' in signal and 'mod:warn' in signal


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
    assert oled.texts() == ['up 1h02m05s', 'heap 120k free', 'drop0 inv3%', 'b4800<9600',
                            'fix1000ms GPS+BD', 'v4000fde', 'e66164084371b26f']


def test_wifi_page_shows_credentials():
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_WIFI, NMEA.Parser(), STATS, 0, False, None, None, WIFI, INFO)
    assert 'NMEABridge-K7X2' in oled.texts() and 'PW abcdefgh2345' in oled.texts()


def test_baud_line_always_fits():
    assert screens._baud_line(4800, None) == 'baud 4800 ?'
    assert screens._baud_line(4800, 4800) == 'baud 4800 ok'
    assert screens._baud_line(115200, 115200) == 'baud 115200 ok'
    assert len(screens._baud_line(115200, 57600)) <= 16 and len(screens._baud_line(115200, None)) <= 16


def worst_case_parser():
    """A busy sky with the widest realistic values: 3-digit PRNs, 12+ satellites per constellation."""
    p = NMEA.Parser()
    p.parse_sentence(with_checksum('GNGGA,235959.999,4807.038,N,01131.000,E,2,12,0.9,545.4,M,46.9,M,,'))
    p.fix_type, p.mode, p.date = 'DGPS', '3D', '31/12/2026'
    p.birds_in_use, p.birds_in_view = '12', 120
    gsv(p, [48, 47, 46, 45, 44, 43, 42, 41, 40, 39, 38, 37], talker='GP')
    gsv(p, [43, 42, 41, 40, 39, 38, 37, 36, 35, 34, 33, 32], talker='BD')
    p.sats_by_talker['GP'][0] = (193, 90, 99)              # QZSS-range PRN, zenith, strongest
    p.parse_sentence(with_checksum('PMTKSPF,3'))
    return p


def worst_case_detectors(p):
    jam = JamDetector(p)
    jam.level, jam.samples, jam.reason = 2, 99, 'CNFM'
    jam.base_mean, jam.base_tracked = 45.4, 12.2
    spoof = SpoofDetector(p)
    spoof.state, spoof.warm_fixes = 'SUSPECT', 999
    spoof._events = {code: 0 for code in spoofing_codes()}
    spoof._alert_at = 0
    return jam, spoof


def spoofing_codes():
    import spoofing
    return spoofing.STRONG + spoofing.MEDIUM + spoofing.WEAK


def test_every_page_fits_with_the_widest_values_and_texts_do_not_overlap():
    p = worst_case_parser()
    jam, spoof = worst_case_detectors(p)
    stats = {'rcvpm': 99999, 'rcv': 99999, 'val': 99999, 'inv': 99999, 'par': 99999, 'ign': 99999}
    info = {'uptime_s': 400 * 86400 + 86399, 'heap': 10 ** 6, 'dropped': 10 ** 7, 'inv_pct': 100,
            'baud': 115200, 'found': 115200, 'fix_ms': 1000, 'gnss': 'GPS+BD', 'uid': 'ffffffffffffffff',
            'now_ms': 0, 'version': '4000fde-dirty'}
    wifi = ('ERR', 'x' * 32, '255.255.255.255', 10, 10, 'p' * 63)
    for found in (115200, 57600, None):
        info['found'] = found
        drawn = []
        for page in nav.PAGES:
            oled = Oled()
            screens.draw(oled, FakeWriter(), page, p, stats, 10 ** 6, False, jam, spoof, wifi, info)
            drawn.append((page, oled))
        check_fits(drawn)
    p.sentence_last_valid_type = p.sentence_last_invalid_type = p.sentence_last_parsed_type = 'GGA'
    p.sentence_last_ignored_type = 'TXT'
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_STATS, p, stats, 0, False, jam, spoof, wifi, info)
    check_fits([(nav.PAGE_STATS, oled)])


def test_counters_are_capped_not_widened():
    assert screens._cap(12, 999) == '12' and screens._cap(999, 999) == '999'
    assert screens._cap(1000, 999) == '999+' and screens._cap(10 ** 9, 9999) == '9999+'


def test_unknown_time_shows_dashes_not_garbled_text():
    p = NMEA.Parser()
    assert p.get_time_string() == '--:--:--'                # the initial '??' used to render as '??::'
    p.time = ''
    assert p.get_time_string() == '--:--:--'
    p.time = '1235'
    assert p.get_time_string() == '--:--:--'
    p.time = '12ab19.000'
    assert p.get_time_string() == '--:--:--'
    p.time = '123519.000'
    assert p.get_time_string() == '12:35:19'
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_MAIN, NMEA.Parser(), STATS, 0, True)
    assert '--:--:--' in oled.texts()


def test_display_signature_ignores_what_no_page_shows_and_type_signature_has_it():
    p = populated_parser()
    before = p.display_signature()
    p.parse_sentence(with_checksum('GPTXT,01,01,02,ANTENNA OK'))      # changes only the "last sentence" data
    assert p.display_signature() == before
    assert p.type_signature()[1] == 'TXT'
    p.cn0_by_talker['GP'] = [c + 0 for c in p.cn0_by_talker['GP']]    # unchanged C/N0 stays unchanged
    assert p.display_signature() == before
    p.parse_sentence(with_checksum('GNGGA,123520,4807.040,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,'))
    assert p.display_signature() != before                           # a new position is visible


def test_cn0_jitter_in_the_decimals_does_not_change_the_signature_but_real_changes_do():
    p = NMEA.Parser()
    gsv(p, [40, 40, 40, 40])
    sig = p.display_signature()
    p.cn0_by_talker['GP'][0] = 41                          # mean 40.0 -> 40.25 and a new maximum: not visible
    assert p.cn0_stats()[1] == 40.25 and p.display_signature() == sig
    p.cn0_by_talker['GP'][0] = 44                          # mean 41.0: the page would show 41
    assert p.display_signature() != sig
    sig = p.display_signature()
    gsv(p, [44, 40, 40, 40, 40])                           # one more satellite tracked
    assert p.display_signature() != sig


class Label:
    """Stand-in for a detector on the main page, which only asks for its label."""
    def __init__(self, text):
        self.text = text

    def label(self):
        return self.text


def title_row(jam_label, spoof_label):
    oled = Oled()
    jam = Label(jam_label) if jam_label is not None else None
    spoof = Label(spoof_label) if spoof_label is not None else None
    screens.draw(oled, FakeWriter(), nav.PAGE_MAIN, NMEA.Parser(), STATS, 0, True, jam, spoof)
    return [(text, x) for text, x, y in oled.calls if y == 3]


def test_main_page_title_row_has_a_space_after_the_time_and_labels_never_touch():
    for jam in (None, '', 'OK', 'LOW', 'JAM?'):
        for spoof in (None, '', 'SPF?', 'SPF!'):
            row = title_row(jam, spoof)
            assert row[0] == ('--:--:--', 0)
            end = 64                                         # the time ends at column 64
            for text, x in row[1:]:
                assert x >= end + 8, (jam, spoof, row)       # at least one blank character between texts
                assert x + 8 * len(text) <= 128, (jam, spoof, row)
                end = x + 8 * len(text)


def test_main_page_title_row_typical_cases():
    assert title_row('OK', '') == [('--:--:--', 0), ('OK', 72)]
    assert title_row('OK', 'SPF!') == [('--:--:--', 0), ('OK', 72), ('SPF!', 96)]
    assert title_row('JAM?', 'SPF?') == [('--:--:--', 0), ('JAM?', 72), ('S?', 112)]   # both: the spoof label is shortened
    assert title_row('LOW', 'SPF!') == [('--:--:--', 0), ('LOW', 72), ('S!', 112)]


def test_stats_page_rows_are_evenly_spaced_with_the_cn_row_clearly_below():
    p = populated_parser()
    jam = JamDetector(p)
    jam.base_mean, jam.base_tracked = 30.0, 10.0
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_STATS, p, STATS, 0, False, jam)
    rows = sorted((y, text) for text, x, y in oled.calls)
    ys = [y for y, _ in rows]
    assert ys[:5] == [0, 10, 20, 30, 40]                    # rx line and the four val/inv/par/ign rows
    assert ys[5] == 54 and ys[5] - ys[4] >= 9 + 5           # the CN row is set apart, not squeezed under ign
    assert rows[5][1].startswith('CN ')
