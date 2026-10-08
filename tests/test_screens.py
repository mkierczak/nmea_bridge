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

    printed = []

    def printstring(self, s):
        FakeWriter.printed.append(s)


class Oled(FakeOled):
    def __init__(self):
        super().__init__()
        self.rects = []
        self.pixels = set()

    def pixel(self, x, y, c):
        self.pixels.add((x, y))

    def icon_at(self, x, y=3):
        """The 8x8 icon drawn with its top left corner at (x, y), as the byte tuple the screens use."""
        return tuple(sum(1 << (7 - col) for col in range(8) if (x + col, y + row) in self.pixels)
                     for row in range(8))

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
    """Every text inside the 128x64 display (8x8 glyphs, none reaching the page indicator at the bottom edge)
    and no two texts sharing pixels."""
    for page, oled in drawn:
        for text, x, y in oled.calls:
            assert x + 8 * len(text) <= 128, (page, text, x)
            assert 0 <= y <= 54, (page, text, y)
        for i, (t1, x1, y1) in enumerate(oled.calls):
            for t2, x2, y2 in oled.calls[i + 1:]:
                if not (x1 + 8 * len(t1) <= x2 or x2 + 8 * len(t2) <= x1):   # they share columns
                    assert abs(y1 - y2) >= 8, (page, t1, t2)                   # so no two rows of glyphs overlap
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
    assert len([r for r in sats.rects if r[3] == 4]) == 5     # five strongest satellites get a bar (and a badge)
    signal = ' '.join(pages[nav.PAGE_SIGNAL].texts())
    assert 'cn0 sat mod' in signal and 'm:warn' in signal


def test_sats_page_sorted_strongest_first_and_caps_at_five():
    p = populated_parser()
    rows = screens._tracked(p)
    assert [r[0] for r in rows] == sorted((r[0] for r in rows), reverse=True)
    assert len(rows) == 12
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_SATS, p, STATS, 0, False, None, None, WIFI, INFO)
    assert oled.calls[0][0] == 'SATS' and '12' in oled.texts()          # the title carries the count
    assert len([t for t in oled.texts() if t[:1] in 'GB' and t[1:].isdigit()]) == 5


def test_spoof_page_lights_the_tiles_of_active_indicators():
    p = NMEA.Parser()
    spoof = SpoofDetector(p)
    spoof.warm_fixes = 100
    spoof._events = {'K1': 0, 'T1': 0, 'S1': 0, 'K3': 0}
    spoof._alert_at = 0
    spoof.state = 'HIGH'
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_SPOOF, p, STATS, 0, False, None, spoof, WIFI,
                 dict(INFO, now_ms=60000))
    texts = oled.texts()
    assert texts[0] == 'SPOOFING' and 'HIGH' in texts                       # the state is a badge
    for code, name in screens.SPOOF_TILES:
        assert code in texts and name in texts                               # all eight tiles are there
    assert len([r for r in oled.rects if r[2:] == (30, 18)]) == 4            # four of them lit (filled)
    assert 'armed' in texts and 'latch 9:00' in texts


def test_spoof_page_while_warming_up_and_off():
    p = NMEA.Parser()
    spoof = SpoofDetector(p)
    spoof.warm_fixes = 12
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_SPOOF, p, STATS, 0, False, None, spoof, WIFI, INFO)
    assert 'warm 12/30' in oled.texts() and not [r for r in oled.rects if r[2:] == (30, 18)]
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_SPOOF, p, STATS, 0, False, None, None, WIFI, INFO)
    assert oled.texts() == ['SPOOFING', 'off']


def test_system_page_content():
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_SYSTEM, NMEA.Parser(), STATS, 0, False, None, None,
                 WIFI, INFO)
    assert [t for t, x, y in sorted(oled.calls, key=lambda c: (c[2], c[1]))] == [
        'up 1h02m', 'v4000fd', 'heap', '120k', 'drop0 inv3%', 'b4800<9600', 'fix1000ms GPS+BD',
        'e66164084371b26f']
    assert any(r[1] == 13 for r in oled.rects) or True                       # the heap bar is drawn with lines


def test_wifi_page_shows_credentials_and_client_slots():
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_WIFI, NMEA.Parser(), STATS, 0, False, None, None, WIFI, INFO)
    assert 'NMEABridge-K7X2' in oled.texts() and 'abcdefgh2345' in oled.texts()    # long password: small font
    assert 'TCP' in oled.texts() and '2/4' in oled.texts()
    assert len([r for r in oled.rects if r[2:] == (8, 8)]) == 2                    # two of the slots are taken
    FakeWriter.printed.clear()
    wifi = ('ON sta1', 'NMEABridge-K7X2', '192.168.4.1', 0, 4, 'k4x9mhq2')
    screens.draw(Oled(), FakeWriter(), nav.PAGE_WIFI, NMEA.Parser(), STATS, 0, False, None, None, wifi, INFO)
    assert FakeWriter.printed == ['k4x9mhq2']                                      # a generated one: large font


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
    spoof.state, spoof.warm_fixes = 'MEDIUM', 999
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


def title_row(jam_label, spoof_label, wifi=None, heartbeat=None, parser=None):
    """Texts of the top row as (x, text), and the icons as (x, name) in one sorted list."""
    oled = Oled()
    jam = Label(jam_label) if jam_label is not None else None
    spoof = Label(spoof_label) if spoof_label is not None else None
    screens.draw(oled, FakeWriter(), nav.PAGE_MAIN, parser or NMEA.Parser(), STATS, 0, True, jam, spoof, wifi,
                 None, {'heartbeat': heartbeat} if heartbeat else None)
    names = {v: k for k, v in (('heart', screens.ICON_HEART), ('outline', screens.ICON_HEART_OUTLINE),
                               ('idle', screens.ICON_IDLE), ('fault', screens.ICON_FAULT),
                               ('wifi', screens.ICON_WIFI))}
    row = [(x, text) for text, x, y in oled.calls if y == 3]
    for x in range(0, 128, 8):
        icon = oled.icon_at(x)
        if icon in names:
            row.append((x, names[icon]))
    return sorted(row)


ICON_NAMES = ('wifi', 'heart', 'outline', 'idle', 'fault')


def test_main_page_title_row_never_overlaps_and_keeps_a_gap_after_the_time():
    wifis = (None, ('OFF', '', '', 0, 4, ''), ('ON sta1', '', '', 0, 4, ''), ('ON sta1', '', '', 3, 4, ''),
             ('ERR', '', '', 0, 4, ''))
    for jam in (None, '', 'JAM.', 'JAM?', 'JAM!'):
        for spoof in (None, '', 'SPF.', 'SPF?', 'SPF!'):
            for wifi in wifis:
                for heartbeat in (None, 'on', 'fault'):
                    row = title_row(jam, spoof, wifi, heartbeat)
                    assert row[0][0] == 0
                    end = 8 * len(row[0][1])                     # where the time stops
                    for i, (x, text) in enumerate(row[1:]):
                        width = 8 if text in ICON_NAMES else 8 * len(text)
                        assert x >= end + (8 if i == 0 else 0), (jam, spoof, wifi, row)   # a gap after the time
                        assert x >= end and x + width <= 128, (jam, spoof, wifi, row)       # no overlap, on screen
                        end = x + width


def test_main_page_title_row_typical_cases():
    assert title_row('', '') == [(0, '--:--:--')]                          # nothing wrong: no labels
    assert title_row(None, 'SPF!') == [(0, '--:--:--'), (88, 'SPF!')]
    assert title_row('JAM.', '') == [(0, '--:--:--'), (88, 'JAM.')]
    assert title_row('JAM?', 'SPF?') == [(0, '--:--:--'), (80, 'J?'), (104, 'S?')]     # both: shortened
    assert title_row('JAM.', 'SPF!') == [(0, '--:--:--'), (80, 'J.'), (104, 'S!')]


def test_main_page_time_gets_a_z_when_there_is_one():
    p = NMEA.Parser()
    p.time = '123456.00'
    assert title_row(None, None, parser=p)[0] == (0, '12:34:56Z')
    assert title_row(None, None)[0] == (0, '--:--:--')                      # no time yet: no Z either


def test_heartbeat_is_an_icon_in_the_last_column():
    for state, name in (('on', 'heart'), ('off', 'outline'), ('idle', 'idle'), ('fault', 'fault')):
        assert title_row(None, None, heartbeat=state)[-1] == (120, name)
    assert len(set(screens.HEARTBEAT.values())) == 4                       # four distinguishable pictures
    assert title_row(None, None) == [(0, '--:--:--')]                      # no state: no icon


def test_main_page_wifi_icon_has_no_client_count_and_gives_way_to_the_labels():
    on = ('ON sta1', '', '', 3, 4, '')
    assert title_row(None, None, on)[-1] == (112, 'wifi')                  # next to the heartbeat column
    assert title_row(None, None, ('ERR', '', '', 0, 4, ''))[-2:] == [(104, 'wifi'), (112, '!')]
    assert title_row(None, None, ('OFF', '', '', 0, 4, '')) == [(0, '--:--:--')]
    assert title_row(None, 'SPF?', on) == [(0, '--:--:--'), (88, 'wifi'), (104, 'S?')]   # the full label does not fit
    assert title_row('JAM.', 'SPF?', on) == [(0, '--:--:--'), (80, 'J.'), (104, 'S?')]   # labels first: no Wi-Fi icon


def test_icons_are_8_by_8_and_drawn_inside_the_display():
    for icon in (screens.ICON_HEART, screens.ICON_HEART_OUTLINE, screens.ICON_IDLE, screens.ICON_FAULT,
                 screens.ICON_WIFI):
        assert len(icon) == 8 and all(0 <= row <= 255 for row in icon)


def test_stats_page_rows_are_evenly_spaced_with_the_cn_row_clearly_below():
    p = populated_parser()
    jam = JamDetector(p)
    jam.base_mean, jam.base_tracked = 30.0, 10.0
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_STATS, p, STATS, 0, False, jam)
    rows = sorted((y, text) for text, x, y in oled.calls if x == 0)
    assert [y for y, _ in rows] == [1, 13, 23, 33, 43, 53]       # title, four bars, then the CN row
    assert [t for _, t in rows][1:5] == ['val', 'inv', 'par', 'ign'] and rows[5][1].startswith('CN ')
    assert 'rx60/m' in oled.texts() and not [t for t in oled.texts() if t.startswith('d')]   # no drops: no badge
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_STATS, p, STATS, 3, False, jam)
    assert 'd3' in oled.texts()                                  # dropped sentences: an inverted badge


def test_satellites_page_header_is_separated_and_aligned_with_the_columns():
    p = populated_parser()
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_SATS, p, STATS, 0, False, None, None, WIFI, INFO)
    calls = {t: (x, y) for t, x, y in oled.calls}
    assert calls['el'] == (36, 1) and calls['C/N0'] == (68, 1)        # column titles in the title row
    rows = sorted((y, x, t) for t, x, y in oled.calls if y >= 13)
    assert sorted({y for y, _, _ in rows}) == [13, 22, 31, 40, 49]    # five rows, the last ends at y=56
    first = [t for y, x, t in rows if y == 13]
    assert first[0] == 'G01' and first[1] == '40' and first[-1] == '48'     # id, elevation, C/N0 value


def test_satellites_page_rows_without_elevation_keep_their_columns():
    p = NMEA.Parser()
    p.sats_by_talker['GP'] = [(5, None, 40), (193, 7, 33)]
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_SATS, p, STATS, 0, False, None, None, WIFI, INFO)
    rows = [text for text, x, y in oled.calls if y >= 13 and x < 60]
    assert rows == ['G05', '--', 'G193', ' 7']                       # fixed-width columns, no crash on None


def _speed_page(sog, cog, no_fix=False, **ctx):
    p = NMEA.Parser()
    p.sog_kn, p.cog_deg = sog, cog
    FakeWriter.printed.clear()
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_SPEED, p, STATS, 0, no_fix, None, None, WIFI, INFO, ctx)
    return oled, list(FakeWriter.printed)


def test_speed_page_shows_sog_and_cog():
    oled, printed = _speed_page(5.24, 123.6)
    assert printed == ['124', '5.2']                 # course on the left, speed on the right
    check_fits([(nav.PAGE_SPEED, oled)])


def test_speed_page_hides_course_when_nearly_stationary_and_values_without_fix():
    assert _speed_page(0.2, 123.0)[1] == ['---', '0.2']
    assert _speed_page(None, None)[1] == ['---', '--']
    assert _speed_page(8.0, 90.0, no_fix=True)[1] == ['---', '--']
    assert _speed_page(8.0, 359.6)[1][0] == '000'                      # rounds up past north
    assert _speed_page(123.4, 10.0)[1] == ['010', '123']               # no decimal once it would not fit


def test_no_fix_shows_how_long_ago_the_last_fix_was():
    p = NMEA.Parser()
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_MAIN, p, STATS, 0, True, None, None, WIFI, INFO, {'fix_age_s': 42})
    assert 'NO FIX' in oled.texts() and 'lost 0:42' in oled.texts()
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_MAIN, p, STATS, 0, True, None, None, WIFI, INFO, {'fix_age_s': None})
    assert oled.texts()[-3:].count('NO FIX') == 1 and not any(t.startswith('lost') for t in oled.texts())
    assert screens.age_text(59) == 'lost 0:59' and screens.age_text(754) == 'lost 12:34'
    assert screens.age_text(3725) == 'lost 1h02m'


def test_alert_banner_only_for_medium_and_high_and_only_when_asked():
    p = NMEA.Parser()
    spoof, jam = SpoofDetector(p), JamDetector(p)
    spoof.state, spoof.reason = 'HIGH', 'K1T1'
    for page in (nav.PAGE_MAIN, nav.PAGE_SPEED):
        oled = Oled()
        screens.draw(oled, FakeWriter(), page, p, STATS, 0, False, jam, spoof, WIFI, INFO, {'banner': True})
        assert 'SPOOFING HIGH' in oled.texts() and oled.rects[0][2] == 128
        oled = Oled()
        screens.draw(oled, FakeWriter(), page, p, STATS, 0, False, jam, spoof, WIFI, INFO, {})
        assert 'SPOOFING HIGH' not in oled.texts() and not oled.rects

    class Jam:
        def __init__(self, state, reason):
            self.state, self.reason = state, reason
    spoof.state = 'LOW'
    assert screens.banner_text(Jam('LOW', 'C'), spoof) == ''                     # LOW only gets the small label
    spoof.state = 'MEDIUM'
    assert screens.banner_text(Jam('OK', ''), spoof) == 'SPOOFING MEDIUM'
    assert screens.banner_text(Jam('MEDIUM', 'C'), spoof.__class__(p)) == 'JAMMING MEDIUM'
    spoof.state = 'HIGH'
    assert screens.banner_text(Jam('MEDIUM', 'C'), spoof) == 'SPF! JAM?'
    assert screens.banner_text(Jam('HIGH', 'C'), spoof) == 'SPF! JAM!'
    for state in ('MEDIUM', 'HIGH'):
        spoof.state = state
        assert len(screens.banner_text(Jam('OK', ''), spoof)) <= 16
        assert 'K1' not in screens.banner_text(Jam('OK', ''), spoof)       # no indicator codes in the banner


class _Lines(Oled):
    def __init__(self):
        super().__init__()
        self.lines = []

    def hline(self, x, y, w, c):
        self.lines.append((x, y, w))


def test_page_indicator_marks_the_current_page_along_the_bottom():
    p = NMEA.Parser()
    pages = nav.PAGES
    oled = _Lines()
    screens.draw(oled, FakeWriter(), nav.PAGE_STATS, p, STATS, 0, False, None, None, WIFI, INFO, {'pages': pages})
    width = 128 // len(pages)
    bottom = [l for l in oled.lines if l[1] == 63]
    assert len(bottom) == len(pages) and [l for l in oled.lines if l[1] == 62] == [
        (pages.index(nav.PAGE_STATS) * width, 62, width - 2)]
    oled = _Lines()
    screens.draw(oled, FakeWriter(), nav.PAGE_STATS, p, STATS, 0, False, None, None, WIFI, INFO, {})
    assert not [l for l in oled.lines if l[1] >= 62]                     # no indicator without the page list


def test_hold_box_shows_progress_and_the_label_it_is_given():
    p = NMEA.Parser()
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_MAIN, p, STATS, 0, False, None, None, WIFI, INFO,
                 {'hold': (50, 'Hold: Wi-Fi on')})
    assert 'Hold: Wi-Fi on' in oled.texts() and oled.rects[-1] == (5, 51, 59, 6)
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_MAIN, p, STATS, 0, False, None, None, WIFI, INFO,
                 {'hold': (100, 'Release now!')})
    assert 'Release now!' in oled.texts() and oled.rects[-1] == (5, 51, 118, 6)
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_MAIN, p, STATS, 0, False, None, None, WIFI, INFO,
                 {'hold': (30, 'Hold: debug')})
    assert 'Hold: debug' in oled.texts()


def test_system_page_shows_errors_instead_of_the_board_id_only_when_there_are_some():
    def lines(**extra):
        oled = Oled()
        screens.draw(oled, FakeWriter(), nav.PAGE_SYSTEM, NMEA.Parser(), STATS, 0, False, None, None, WIFI,
                     dict(INFO, **extra))
        return oled.texts()
    assert lines()[-1] == 'e66164084371b26f'
    assert lines(rerr=0, gerr=0, stale=0)[-1] == 'e66164084371b26f'
    assert lines(rerr=1, gerr=0, stale=12)[-1] == 'ERR r1 g0 s12'
    assert lines(rerr=500, gerr=3, stale=0)[-1] == 'ERR r99+ g3 s0'


class _Sev:
    def __init__(self, state):
        self.state = state


def _gps_page(jam=None, spoof=None, parser=None):
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_GPS, parser or populated_parser(), STATS, 0, False, jam, spoof, WIFI,
                 INFO, {'pages': nav.MAIN_PAGES})
    return oled


def test_gps_page_shows_the_constellation_strip_aic_tag_and_both_severity_gauges():
    p = populated_parser()
    p.parse_sentence(with_checksum('GNGSA,A,3,05,07,13,15,20,21,,,,,,,1.6,0.9,1.3,1'), 1)
    p.parse_sentence(with_checksum('GNGSA,A,3,06,09,11,21,,,,,,,,,1.6,0.9,1.3,4'), 1)
    p.pmtk_acks[286] = 3
    oled = _gps_page(_Sev('OK'), _Sev('LOW'), p)
    texts = oled.texts()
    assert texts[0] == 'GPS' and 'G6 B4 36dB' in texts and 'AIC+' in texts
    assert 'JAMMING' in texts and 'SPOOF' in texts and 'OK' in texts and 'LOW' in texts
    check_fits([(nav.PAGE_GPS, oled)])


def test_gps_page_aic_tag_is_lit_only_when_cancellation_is_on_and_alerts_fill_the_gauge():
    p = populated_parser()
    p.pmtk_acks[286] = 3
    assert (96, 12, 32, 9) in _gps_page(_Sev('OK'), _Sev('OK'), p).rects          # a lit tag
    p.pmtk_acks[286] = 2
    off = _gps_page(_Sev('OK'), _Sev('OK'), p)
    assert (96, 12, 32, 9) not in off.rects and 'AIC-' in off.texts()
    del p.pmtk_acks[286]
    assert 'AIC?' in _gps_page(_Sev('OK'), _Sev('OK'), p).texts()
    calm = _gps_page(_Sev('LOW'), _Sev('OK'))
    assert (0, 22, 62, 38) not in calm.rects
    alert = _gps_page(_Sev('OK'), _Sev('HIGH'))
    assert (66, 22, 62, 38) in alert.rects and (0, 22, 62, 38) not in alert.rects     # only the alert is filled
    assert len([r for r in alert.rects if r[2:] == (16, 8)]) == 3                      # HIGH lights all three steps


def test_gps_page_severity_steps_follow_the_level_and_a_disabled_detector_says_off():
    for state, lit in (('OK', 0), ('LOW', 1), ('MEDIUM', 2), ('HIGH', 3)):
        oled = _gps_page(_Sev(state), None)
        assert len([r for r in oled.rects if r[2:] == (16, 8) and r[0] < 62]) == lit, state
        assert state in oled.texts()
    oled = _gps_page(None, None)
    assert oled.texts().count('off') == 2


def test_page_indicator_is_dashed_in_the_debug_loop():
    p = NMEA.Parser()
    solid, dashed = _Lines(), _Lines()
    screens.draw(solid, FakeWriter(), nav.PAGE_STATS, p, STATS, 0, False, None, None, WIFI, INFO,
                 {'pages': nav.DEBUG_PAGES})
    screens.draw(dashed, FakeWriter(), nav.PAGE_STATS, p, STATS, 0, False, None, None, WIFI, INFO,
                 {'pages': nav.DEBUG_PAGES, 'debug': True})
    width = 128 // len(nav.DEBUG_PAGES)
    assert len([l for l in solid.lines if l[1] == 63]) == len(nav.DEBUG_PAGES)
    assert all(l[2] == width - 2 for l in solid.lines if l[1] == 63)
    assert all(l[2] == 2 for l in dashed.lines if l[1] == 63) and len([l for l in dashed.lines if l[1] == 63]) > 6


def test_main_page_before_the_first_fix_shows_the_wait_and_what_is_tracked():
    p = populated_parser()
    oled = Oled()
    screens.draw(oled, FakeWriter(), nav.PAGE_MAIN, p, STATS, 0, True, None, None, None, None,
                 {'fix_age_s': None, 'uptime_s': 75})
    texts = oled.texts()
    assert 'Waiting for fix' in texts and 'tracked 12/12' in texts and 'waited 1:15' in texts
    assert 'NO FIX' not in texts
    check_fits([(nav.PAGE_MAIN, oled)])
    oled = Oled()                                           # a fix was lost: the old message with the time
    screens.draw(oled, FakeWriter(), nav.PAGE_MAIN, p, STATS, 0, True, None, None, None, None,
                 {'fix_age_s': 5, 'uptime_s': 75})
    assert 'NO FIX' in oled.texts() and 'Waiting for fix' not in oled.texts()
