"""Render the screens of docs/screens.md as PNG images, with the same code that draws them on the board.

    python3 tools/screenshots.py [OUTDIR]        (default: docs/img; needs Pillow: pip install pillow)

screens.draw() and the menu are run against a simulated 128 x 64 display that uses MicroPython's own 8x8
text font and the roboto14 font of the position, so the pictures show the real layout and glyphs. The
data shown is made up (a boat near Stockholm); detector states are set by hand to show alerts.
"""
import os
import sys
import tempfile
import types

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import roboto14                                   # noqa: E402
from screenshot_font import FONT                  # noqa: E402

WIDTH, HEIGHT = 128, 64
SCALE = 4
LIT, DARK, OFF = (214, 236, 255), (4, 9, 16), (14, 22, 34)


class SimOled(object):
    """The drawing calls the screens use, on a list of pixel rows."""

    def __init__(self):
        self.px = [[0] * WIDTH for _ in range(HEIGHT)]

    def pixel(self, x, y, c):
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            self.px[y][x] = c

    def fill(self, c):
        self.px = [[c] * WIDTH for _ in range(HEIGHT)]

    def fill_rect(self, x, y, w, h, c):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.pixel(xx, yy, c)

    def hline(self, x, y, w, c):
        self.fill_rect(x, y, w, 1, c)

    def vline(self, x, y, h, c):
        self.fill_rect(x, y, 1, h, c)

    def text(self, s, x, y, c=1):
        for i, ch in enumerate(s):
            code = ord(ch)
            glyph = FONT[(code if 32 <= code <= 127 else 127) - 32]
            for col in range(8):
                for row in range(8):
                    if glyph[col] >> row & 1:
                        self.pixel(x + 8 * i + col, y + row, c)

    def show(self):
        pass


class SimWriter(object):
    """writer.Writer for the large font: the same positions and advances, drawn from roboto14's bitmaps."""
    row = col = 0

    def __init__(self, oled=None, font=None, *args, **kwargs):
        self.oled = oled

    @staticmethod
    def set_textpos(oled, row, col):
        SimWriter.row, SimWriter.col = row, col

    @staticmethod
    def stringlen(string):
        return sum(roboto14.get_ch(ch)[2] for ch in string)

    def printstring(self, string):
        for ch in string:
            glyph, height, width = roboto14.get_ch(ch)
            per_row = (width - 1) // 8 + 1
            for y in range(height):
                for x in range(width):
                    if glyph[y * per_row + x // 8] >> (7 - x % 8) & 1:
                        self.oled.pixel(SimWriter.col + x, SimWriter.row + y, 1)
            SimWriter.col += width


# screens.py does `from writer import Writer`; the real one needs MicroPython's framebuf
_writer = types.ModuleType('writer')
_writer.Writer = SimWriter
sys.modules['writer'] = _writer

import NMEA                                       # noqa: E402
import jamming                                    # noqa: E402
import menu                                       # noqa: E402
import nav                                        # noqa: E402
import screens                                    # noqa: E402
import settings                                   # noqa: E402
import spoofing                                   # noqa: E402


def with_checksum(body):
    c = 0
    for ch in body:
        c ^= ord(ch)
    return '${}*{:02X}'.format(body, c)


def gsv(parser, talker, sats):
    """sats: [(prn, elevation, azimuth, cn0)]"""
    groups = [sats[i:i + 4] for i in range(0, len(sats), 4)]
    for m, group in enumerate(groups, 1):
        fields = ''.join(',{:02d},{},{},{}'.format(*s) for s in group)
        parser.parse_sentence(with_checksum('{}GSV,{},{},{:02d}{}'.format(talker, len(groups), m, len(sats), fields)))


def make_parser():
    p = NMEA.Parser()
    for body in ('GNGGA,123456.00,5918.3420,N,01803.2150,E,1,09,0.9,12.5,M,23.0,M,,',
                 'GNRMC,123456.00,A,5918.3420,N,01803.2150,E,5.24,123.6,080626,,,A',
                 'GNGSA,A,3,05,07,13,15,20,21,,,,,,,1.6,0.9,1.3,1',
                 'GNGSA,A,3,06,09,11,21,,,,,,,,,1.6,0.9,1.3,4',
                 'GNZDA,123456.00,08,06,2026,00,00'):
        p.parse_sentence(with_checksum(body), 1000)
    gsv(p, 'GP', [(5, 62, 83, 45), (7, 41, 110, 42), (13, 33, 200, 40), (15, 25, 310, 37),
                  (20, 18, 250, 34), (21, 12, 150, 31)])
    gsv(p, 'BD', [(6, 55, 90, 41), (9, 38, 130, 39), (11, 21, 220, 33), (21, 9, 300, 28)])
    p.parse_sentence(with_checksum('PMTKSPF,1'))
    p.pmtk_acks[286] = 3
    return p


class Jam(object):
    base_mean, base_tracked = 41.0, 10.0

    def __init__(self, state='OK', reason=''):
        self.state, self.reason = state, reason

    def label(self):
        return {'LOW': 'JAM.', 'MEDIUM': 'JAM?', 'HIGH': 'JAM!'}.get(self.state, '')


def make_spoof(parser, state, events, reason):
    s = spoofing.SpoofDetector(parser)
    s.warm_fixes = 100
    s._events = {code: 0 for code in events}
    s.state, s.reason = state, reason
    if state == 'HIGH':
        s._alert_at = 0
    return s


STATS = {'rcvpm': 780, 'rcv': 130, 'val': 129, 'inv': 1, 'par': 120, 'ign': 9}
INFO = {'uptime_s': 5025, 'heap': 98304, 'dropped': 0, 'inv_pct': 1, 'baud': 4800, 'found': 4800,
        'fix_ms': 1000, 'gnss': 'GPS+BD', 'uid': 'e66164084371b26f', 'now_ms': 60000, 'version': '3fc470a',
        'rerr': 0, 'gerr': 0, 'stale': 0}
WIFI = ('ON sta1', 'NMEABridge-K7X2', '192.168.4.1', 1, 4, 'k4x9mhq2')


def page_scene(page, parser=None, no_fix=False, jam=None, spoof=None, info=INFO, **ctx):
    def render():
        oled = SimOled()
        p = parser or make_parser()
        debug = page in nav.DEBUG_PAGES      # the debug loop has its own page indicator (dashed)
        screens.draw(oled, SimWriter(oled), page, p, STATS, 0, no_fix, jam, spoof, WIFI, info,
                     dict({'pages': nav.DEBUG_PAGES if debug else nav.MAIN_PAGES, 'debug': debug}, **ctx))
        return oled
    return render


def menu_scene(group, cursor, change=None):
    def render():
        defaults = {'gps_baud': 4800, 'gnss_mode': 'GPS+BD', 'jam_detect': True, 'spoof_detect': True,
                    'spoof_action': 'display', 'contrast': 0, 'screen_off_s': 0, 'night': False, 'speed_unit': 'kn',
                    'coord_fmt': 'ddmm.mm', 'utc_offset_h': 0, 'log_raw': False}
        defaults.update({'fwd_' + t: True for t in ('RMC', 'GGA', 'GSA', 'GSV', 'ZDA')})
        defaults.update(settings.threshold_defaults(jamming, spoofing))
        for row in settings.SCHEMA:             # anything not listed above: the first choice, False or the minimum
            defaults.setdefault(row[0], False if row[2] == settings.BOOL else row[5][0])
        with tempfile.TemporaryDirectory() as d:
            cfg = settings.Settings(defaults, os.path.join(d, 'settings.json'))
            if change:
                cfg.set(*change)
            m = menu.Menu(cfg, {'apply': lambda k: None, 'reset': lambda: None, 'reboot': lambda: None})
            m.name, m.cursor = group, cursor
            m._scroll()
            oled = SimOled()
            m.draw(oled)
        return oled
    return render


def scenes():
    p = make_parser()
    alert_spoof = make_spoof(p, 'HIGH', ('K1', 'T1', 'S1', 'K3'), 'K1T1S1K3')
    suspect_spoof = make_spoof(p, 'MEDIUM', ('S1',), 'S1')
    nofix = make_parser()
    nofix.fix_type = 'NO'
    return [
        ('main', 'Main', page_scene(nav.PAGE_MAIN, p, jam=Jam('OK'), spoof=make_spoof(p, 'OK', (), ''),
                                    heartbeat='on')),
        ('main-suspect', 'Main, low jamming and medium spoofing probability',
         page_scene(nav.PAGE_MAIN, p, jam=Jam('LOW', 'C'), spoof=suspect_spoof, heartbeat='off')),
        ('main-fault', 'Main, radio write failure',
         page_scene(nav.PAGE_MAIN, p, jam=Jam('OK'), spoof=make_spoof(p, 'OK', (), ''), heartbeat='fault')),
        ('main-alert', 'Main, spoofing alert banner',
         page_scene(nav.PAGE_MAIN, p, jam=Jam('OK'), spoof=alert_spoof, banner=True)),
        ('main-nofix', 'Main, no fix', page_scene(nav.PAGE_MAIN, nofix, no_fix=True, jam=Jam('OK'), fix_age_s=42, heartbeat='idle')),
        ('main-waiting', 'Main, waiting for the first fix',
         page_scene(nav.PAGE_MAIN, nofix, no_fix=True, jam=Jam('OK'), uptime_s=75, heartbeat='idle')),
        ('main-wifi-hold', 'Holding UP for the Wi-Fi gesture',
         page_scene(nav.PAGE_MAIN, p, jam=Jam('OK'), hold=(55, 'Hold: Wi-Fi on'))),
        ('main-debug-hold', 'Holding both keys for the debug loop',
         page_scene(nav.PAGE_MAIN, p, jam=Jam('OK'), hold=(70, 'Hold: debug'))),
        ('speed', 'Speed', page_scene(nav.PAGE_SPEED, p, sog_trend=1)),
        ('gps', 'GPS', page_scene(nav.PAGE_GPS, p, jam=Jam('LOW', 'C'), spoof=make_spoof(p, 'OK', (), ''))),
        ('gps-alert', 'GPS with a spoofing alert',
         page_scene(nav.PAGE_GPS, p, jam=Jam('OK'), spoof=alert_spoof)),
        ('speed-alert', 'Speed, jamming alert banner',
         page_scene(nav.PAGE_SPEED, p, jam=Jam('MEDIUM', 'CN'), banner=True)),
        ('alerts', 'Alerts', page_scene(nav.PAGE_LOG, p, alerts=[('12:41', 'SPF!', 'K1T1S'), ('12:36', 'JAM?', 'CN'),
                                                                 ('12:12', 'SPF?', 'S1')], alert_total=3)),
        ('stats', 'Stats', page_scene(nav.PAGE_STATS, p, jam=Jam('OK'))),
        ('satellites', 'Satellites', page_scene(nav.PAGE_SATS, p)),
        ('signal', 'Signal', page_scene(nav.PAGE_SIGNAL, p, jam=Jam('MEDIUM', 'CN'))),
        ('spoofing', 'Spoofing', page_scene(nav.PAGE_SPOOF, p, spoof=alert_spoof)),
        ('system', 'System', page_scene(nav.PAGE_SYSTEM, p)),
        ('system-errors', 'System with contained errors',
         page_scene(nav.PAGE_SYSTEM, p, info=dict(INFO, rerr=1, gerr=0, stale=12))),
        ('debug', 'Debug', page_scene(nav.PAGE_DEBUG, p, jam=Jam('OK'), spoof=suspect_spoof)),
        ('wifi', 'Wi-Fi', page_scene(nav.PAGE_WIFI, p)),
        ('menu', 'Menu', menu_scene('', 1)),
        ('menu-advanced', 'Advanced menu with the default shown',
         menu_scene('Advanced', 4, change=('max_speed_kn', 40))),
    ]


def to_image(oled):
    from PIL import Image, ImageDraw
    img = Image.new('RGB', (WIDTH * SCALE + 16, HEIGHT * SCALE + 16), DARK)
    draw = ImageDraw.Draw(img)
    for y, row in enumerate(oled.px):
        for x, v in enumerate(row):
            x0, y0 = 8 + x * SCALE, 8 + y * SCALE
            draw.rectangle((x0, y0, x0 + SCALE - 2, y0 + SCALE - 2), fill=LIT if v else OFF)
    return img


def main(outdir):
    os.makedirs(outdir, exist_ok=True)
    names = []
    for name, title, render in scenes():
        to_image(render()).save(os.path.join(outdir, name + '.png'))
        names.append((name, title))
    return names


if __name__ == '__main__':
    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, 'docs', 'img')
    for n, t in main(out):
        print('{}.png  {}'.format(n, t))
