from writer import Writer
from nav import (PAGE_MAIN, PAGE_STATS, PAGE_SATS, PAGE_SIGNAL, PAGE_SPOOF, PAGE_SYSTEM,
                 PAGE_DEBUG, PAGE_WIFI, PAGE_SPEED, PAGE_GPS)

JAM_WORDS = {'C': 'cn0', 'N': 'sat', 'F': 'fix', 'M': 'mod'}   # short enough for all four on one line
# the annunciator tiles of the Spoofing page: indicator code and a three-letter name
SPOOF_TILES = (('K1', 'jmp'), ('T1', 'tim'), ('K2', 'spd'), ('S1', 'flt'),
               ('C1', 'g/b'), ('K3', 'alt'), ('S2', 'elv'), ('S3', 'pwr'))
MODULE_JAM = {0: '?', 1: 'ok', 2: 'warn', 3: 'CRIT'}


ALERT_STATES = ('MEDIUM', 'HIGH')
MARKS = {'LOW': '.', 'MEDIUM': '?', 'HIGH': '!'}     # after SPF / JAM: SPF. low, SPF? medium, SPF! high


def banner_text(jam, spoof):
    """Text of the alert banner, or '' when nothing is at MEDIUM or HIGH (LOW only gets the small label):
    'SPOOFING HIGH', 'JAMMING MEDIUM', or 'SPF! JAM?' (the marks stand for the levels) when both are."""
    spf = spoof.state if spoof and spoof.state in ALERT_STATES else ''
    jm = jam.state if jam and jam.state in ALERT_STATES else ''
    if spf and jm:
        return 'SPF{} JAM{}'.format(MARKS[spf], MARKS[jm])
    if spf:
        return 'SPOOFING ' + spf
    if jm:
        return 'JAMMING ' + jm
    return ''


# 8x8 icons, one byte per row, the most significant bit on the left
ICON_HEART = (0b00000000, 0b01100110, 0b11111111, 0b11111111, 0b01111110, 0b00111100, 0b00011000, 0b00000000)
ICON_HEART_OUTLINE = (0b00000000, 0b01100110, 0b10011001, 0b10000001, 0b01000010, 0b00100100, 0b00011000,
                      0b00000000)
ICON_IDLE = (0b00000000, 0b00000000, 0b00000000, 0b00111100, 0b00111100, 0b00000000, 0b00000000, 0b00000000)
ICON_FAULT = (0b10000001, 0b01000010, 0b00100100, 0b00011000, 0b00011000, 0b00100100, 0b01000010, 0b10000001)
ICON_WIFI = (0b00111100, 0b01000010, 0b10000001, 0b00111100, 0b01000010, 0b00011000, 0b00000000, 0b00011000)
HEARTBEAT = {'on': ICON_HEART, 'off': ICON_HEART_OUTLINE, 'idle': ICON_IDLE, 'fault': ICON_FAULT}
STATUS_SLOTS = 5          # character cells (x = 80..119) between the time and the heartbeat


def _icon(oled, bitmap, x, y):
    for row in range(8):
        bits = bitmap[row]
        for col in range(8):
            if bits >> (7 - col) & 1:
                oled.pixel(x + col, y + row, 1)


def _wifi_mark(wifi):
    """(icon, text) for the Wi-Fi access point: the arcs alone when it is on, with '!' when it failed to start;
    None when it is off. (The number of clients is on the Wi-Fi page.)"""
    if not wifi:
        return None
    if wifi[0].startswith('ON'):
        return ICON_WIFI, ''
    return (ICON_WIFI, '!') if wifi[0] == 'ERR' else None


def _short(label):
    """'SPF?' -> 'S?', 'JAM!' -> 'J!'."""
    return label[0] + label[-1]


def _status_row(oled, parser, jam, spoof, wifi, heartbeat):
    """Top row of the Main page: UTC time with a Z, then (right to left) the heartbeat icon, the spoofing label,
    the jamming label (only when something is wrong) and the Wi-Fi icon. When they do not all fit the labels
    shrink, and the Wi-Fi mark gives way first."""
    time_text = parser.get_time_string()
    oled.text(time_text if time_text[0] == '-' else time_text + 'Z', 0, 3, 1)
    if heartbeat:
        _icon(oled, HEARTBEAT[heartbeat], 120, 3)
    jam_label = jam.label() if jam else ''
    if jam_label == 'OK':
        jam_label = ''                    # nothing to report: no label
    mark = _wifi_mark(wifi)
    items = [(spoof.label() if spoof else '', True, None), (jam_label, True, None),
             (mark[1] if mark else '', False, mark[0] if mark else None)]
    items = [item for item in items if item[0] or item[2]]

    def layout(shrunk):
        texts = [(_short(t) if shrunk and c else t, c, i) for t, c, i in items]
        widths = [len(t) + (1 if i else 0) for t, c, i in texts]
        return texts, widths, sum(widths) + len(widths) - 1
    texts, widths, slots = layout(False)
    if slots > STATUS_SLOTS:
        texts, widths, slots = layout(True)
    if slots > STATUS_SLOTS and items and items[-1][2]:        # still too much: drop the Wi-Fi mark
        items = items[:-1]
        texts, widths, slots = layout(True)
    x = 120
    for (text, _, icon), width in zip(texts, widths):
        x -= 8 * width
        if icon:
            _icon(oled, icon, x, 3)
            if text:
                oled.text(text, x + 8, 3, 1)
        else:
            oled.text(text, x, 3, 1)
        x -= 8


def _banner(oled, text, height):
    """Inverted bar across the top: white background, black text."""
    oled.fill_rect(0, 0, 128, height, 1)
    oled.text(text, 0, (height - 8) // 2, 0)


def age_text(seconds):
    """'lost 0:42' / 'lost 1h02m': how long ago the last fix was."""
    if seconds < 3600:
        return 'lost {}:{:02d}'.format(seconds // 60, seconds % 60)
    return 'lost {}h{:02d}m'.format(seconds // 3600, seconds // 60 % 60)


def _page_indicator(oled, page, pages, debug=False):
    """One segment per page along the bottom edge (two pixels high for the current page). In the debug loop the
    segments are dashed, so it is clear which loop you are in."""
    if not pages or page not in pages:
        return
    width = 128 // len(pages)
    for i, p in enumerate(pages):
        x = i * width
        for dx in range(0, width - 2, 4 if debug else width):
            length = 2 if debug else width - 2
            oled.hline(x + dx, 63, length, 1)
            if p == page:
                oled.hline(x + dx, 62, length, 1)


def _draw_hold(oled, hold):
    """Progress box while a key gesture is held (Wi-Fi toggle, switching loops); hold = (percent, label)."""
    percent, label = hold
    oled.fill_rect(0, 34, 128, 28, 0)
    oled.hline(0, 34, 128, 1)
    oled.hline(0, 61, 128, 1)
    oled.text(label, (128 - 8 * len(label)) // 2, 38, 1)
    oled.fill_rect(4, 50, 120, 8, 1)
    oled.fill_rect(5, 51, 118, 6, 0)
    oled.fill_rect(5, 51, 118 * min(percent, 100) // 100, 6, 1)


def _frame(oled, x, y, w, h):
    """Outline with the corners cut by a pixel, like an instrument bezel."""
    oled.hline(x + 2, y, w - 4, 1)
    oled.hline(x + 2, y + h - 1, w - 4, 1)
    oled.vline(x, y + 2, h - 4, 1)
    oled.vline(x + w - 1, y + 2, h - 4, 1)
    for cx, cy in ((x + 1, y + 1), (x + w - 2, y + 1), (x + 1, y + h - 2), (x + w - 2, y + h - 2)):
        oled.hline(cx, cy, 1, 1)


def _width(font, text):
    """Pixel width of text in the large font (a fixed 12 px per character if the font cannot say)."""
    measure = getattr(font, 'stringlen', None)
    return measure(text) if measure else 12 * len(text)


def _gauge(oled, font, x, title, value, unit):
    """One framed instrument 62 pixels wide: title above, value in the large font, unit below."""
    if title:
        oled.text(title, x + (62 - 8 * len(title)) // 2, 2, 1)
    _frame(oled, x, 12, 62, 48)
    Writer.set_textpos(oled, 23, x + (62 - _width(font, value)) // 2)
    font.printstring(value)
    oled.text(unit, x + (62 - 8 * len(unit)) // 2, 43, 1)


def _draw_speed(oled, font_large, parser, no_fix, banner):
    """Cockpit panel: COG on the left, SOG on the right, each in its own frame. The title row is where the
    alert banner goes."""
    sog = parser.sog_kn
    moving = not no_fix and sog is not None
    if banner:
        _banner(oled, banner, 10)
    speed = '--' if not moving else '{:.1f}'.format(sog) if sog < 100 else '{:.0f}'.format(sog)
    # a course over ground is meaningless while (nearly) stationary
    cog = parser.cog_deg
    course = '{:03d}'.format(round(cog) % 360) if moving and sog >= 0.5 and cog is not None else '---'
    _gauge(oled, font_large, 0, '' if banner else 'COG', course, 'deg')      # the banner covers the titles
    _gauge(oled, font_large, 66, '' if banner else 'SOG', speed, 'kn')


def _title(oled, text, right='', badge=''):
    """Title row of the instrument pages: the name on the left, optionally a plain text and an inverted badge
    (the state) on the right, and a rule below."""
    oled.text(text, 0, 1, 1)
    edge = 128
    if badge:
        width = 8 * len(badge) + 4
        oled.fill_rect(edge - width, 0, width, 9, 1)
        oled.text(badge, edge - width + 2, 1, 0)
        edge -= width + 4
    if right and edge - 8 * len(right) >= 8 * len(text) + 8:     # the plain text gives way when it does not fit
        oled.text(right, edge - 8 * len(right), 1, 1)
    oled.hline(0, 10, 128, 1)


def _hbar(oled, x, y, w, h, percent):
    """Horizontal gauge: an outline with a filled part of percent (0-100) of its inside."""
    oled.hline(x, y, w, 1)
    oled.hline(x, y + h - 1, w, 1)
    oled.vline(x, y, h, 1)
    oled.vline(x + w - 1, y, h, 1)
    inner = (w - 4) * max(0, min(100, percent)) // 100
    if inner:
        oled.fill_rect(x + 2, y + 2, inner, h - 4, 1)


def _readout(oled, font, x, label, value, base):
    """A framed 62 x 28 instrument: label at the top, the value in the large font and the baseline it is
    compared with in the small font next to it."""
    _frame(oled, x, 12, 62, 28)
    oled.text(label, x + (62 - 8 * len(label)) // 2, 14, 1)
    total = _width(font, value) + 8 * len(base)
    left = x + (62 - total) // 2
    Writer.set_textpos(oled, 23, left)
    font.printstring(value)
    oled.text(base, left + _width(font, value), 29, 1)


def draw(oled, font_large, screen, parser, stats, dropped, no_fix, jam=None, spoof=None, wifi=None, info=None,
         ctx=None):
    """Render one screen into the frame buffer (caller calls oled.show()). ctx (optional dict) carries what
    only the controller knows: 'pages' (page indicator), 'fix_age_s' (seconds since the last fix, None if
    never), 'banner' (True to show the alert banner), 'heartbeat' (key of HEARTBEAT, Main page), 'debug'
    (True in the debug loop) and 'hold' (key gesture progress, see _draw_hold). 'wifi' is used by the Wi-Fi page and, as a small icon, by the
    Main page."""
    ctx = ctx or {}
    banner = banner_text(jam, spoof) if ctx.get('banner') else ''
    oled.fill(0)
    if screen == PAGE_SPEED:
        _draw_speed(oled, font_large, parser, no_fix, banner)
    elif screen == PAGE_MAIN:
        _draw_main(oled, font_large, parser, no_fix, jam, spoof, wifi, banner, ctx)
    elif screen == PAGE_GPS:
        _draw_gps(oled, parser, jam, spoof)
    elif screen == PAGE_STATS:
        _draw_stats(oled, parser, stats, dropped, jam)
    elif screen == PAGE_SATS:
        _draw_sats(oled, parser)
    elif screen == PAGE_SIGNAL:
        _draw_signal(oled, font_large, parser, jam)
    elif screen == PAGE_SPOOF:
        _draw_spoof(oled, spoof, info)
    elif screen == PAGE_SYSTEM:
        _draw_system(oled, info)
    elif screen == PAGE_DEBUG:
        _draw_debug(oled, font_large, parser, jam, spoof)
    elif screen == PAGE_WIFI:
        _draw_wifi(oled, font_large, wifi)
    _page_indicator(oled, screen, ctx.get('pages'), ctx.get('debug'))
    if ctx.get('hold'):
        _draw_hold(oled, ctx['hold'])


LEVELS = {'OK': 0, 'LOW': 1, 'MEDIUM': 2, 'HIGH': 3}


def _severity(oled, x, label, state):
    """A framed 62 x 38 gauge: the name, three meter steps (LOW, MEDIUM, HIGH light up one after the other) and the
    level word. An alert (MEDIUM, HIGH) fills the whole panel, so it cannot be missed. state None = detector off."""
    alert = state in ALERT_STATES
    color = 0 if alert else 1
    if alert:
        oled.fill_rect(x, 22, 62, 38, 1)
    else:
        _frame(oled, x, 22, 62, 38)
    oled.text(label, x + (62 - 8 * len(label)) // 2, 25, color)
    level = LEVELS.get(state, 0)
    for step in range(3):
        sx = x + 5 + 18 * step
        if step < level:
            oled.fill_rect(sx, 36, 16, 8, color)
        else:
            oled.hline(sx, 36, 16, color)
            oled.hline(sx, 43, 16, color)
            oled.vline(sx, 36, 8, color)
            oled.vline(sx + 15, 36, 8, color)
    word = 'off' if state is None else state
    oled.text(word, x + (62 - 8 * len(word)) // 2, 48, color)


def _draw_gps(oled, parser, jam, spoof):
    """Constellation stats on a strip, the AIC status as a small tag, and the two severity gauges."""
    mode = parser.mode or parser.fix_type
    _title(oled, 'GPS', mode, '{}/{}'.format(parser.birds_in_use, parser.birds_in_view))
    tracked, mean, _ = parser.cn0_stats()
    oled.text('G{} B{} {}dB'.format(parser.birds_GPS, parser.birds_BD, round(mean) if tracked else '--'), 0, 13, 1)
    aic = parser.pmtk_acks.get(286)
    if aic == 3:                                   # interference cancellation is on: a lit tag
        oled.fill_rect(96, 12, 32, 9, 1)
        oled.text('AIC+', 96, 13, 0)
    else:
        _frame(oled, 96, 12, 32, 9)
        oled.text('AIC' + ('?' if aic is None else '-'), 96, 13, 1)
    _severity(oled, 0, 'JAMMING', jam.state if jam else None)
    _severity(oled, 66, 'SPOOF', spoof.state if spoof else None)


def _draw_main(oled, font_large, parser, no_fix, jam, spoof, wifi, banner, ctx):
    if banner:
        _banner(oled, banner, 13)
    else:
        _status_row(oled, parser, jam, spoof, wifi, ctx.get('heartbeat'))
    oled.hline(0, 14, 128, 1)
    if no_fix:
        oled.text('NO FIX', 40, 28, 1)
        age = ctx.get('fix_age_s')
        if age is not None:
            text = age_text(age)
            oled.text(text, (128 - 8 * len(text)) // 2, 38, 1)
    else:
        Writer.set_textpos(oled, 17, 0)
        font_large.printstring(parser.get_lat_string())
        Writer.set_textpos(oled, 32, 0)
        font_large.printstring(parser.get_lon_string())
    oled.hline(0, 48, 128, 1)
    oled.text((parser.fix_type + ' ' + parser.mode + ' ' +
               str(parser.birds_in_use) + '/' + str(parser.birds_in_view))[:13], 0, 54, 1)  # ends before x=104
    oled.text(parser.get_dop_string(type='PDOP') + parser.get_dop_string(type='HDOP') +
              parser.get_dop_string(type='VDOP'), 104, 54, 1)


def _cap(value, limit):
    """Clip a counter for display: '9999+' instead of a number too wide for its line."""
    return str(value) if value <= limit else '{}+'.format(limit)


def _draw_stats(oled, parser, stats, dropped, jam):
    """Sentence statistics: a bar per category with its share of the last 10 s, and the last type seen."""
    rcv = stats['rcv'] or 1  # avoid division by zero in empty windows
    _title(oled, 'STATS', 'rx{}/m'.format(_cap(round(stats['rcvpm']), 9999)),
           'd{}'.format(_cap(dropped, 99)) if dropped else '')
    for row, (key, last) in enumerate((('val', parser.sentence_last_valid_type),
                                       ('inv', parser.sentence_last_invalid_type),
                                       ('par', parser.sentence_last_parsed_type),
                                       ('ign', parser.sentence_last_ignored_type))):
        y = 13 + 10 * row
        percent = round(stats[key] / rcv * 100)
        oled.text(key, 0, y, 1)
        _hbar(oled, 26, y, 40, 8, percent)
        oled.text('{:>4}'.format(str(percent) + '%'), 70, y, 1)
        oled.text(last[:3], 104, y, 1)
    if jam:
        tracked, mean, _ = parser.cn0_stats()
        oled.text('CN {}/{} n{}/{}'.format(round(mean), round(jam.base_mean), tracked,
                                           round(jam.base_tracked)), 0, 53, 1)   # 16 chars at most


def _group(talker):
    return 'G' if talker in ('GP', 'GN') else 'B' if talker in ('BD', 'GB') else talker[:1]


def _tracked(parser):
    """[(cn0, group, prn, elevation)] of tracked satellites, strongest first."""
    rows = []
    for talker, lst in parser.sats_by_talker.items():
        for prn, el, cn in lst:
            if cn > 0:
                rows.append((cn, _group(talker), prn, el))
    rows.sort(reverse=True)
    return rows


def _draw_sats(oled, parser):
    """The five strongest tracked satellites: id (G = GPS, B = BeiDou + PRN), elevation, a C/N0 gauge (full at
    50 dB-Hz, a tick at 35) and the C/N0 value; the title row counts the satellites tracked."""
    rows = _tracked(parser)
    _title(oled, 'SATS', badge=str(len(rows)) if rows else '')
    oled.text('el', 36, 1, 1)                               # column titles, over the columns below
    oled.text('C/N0', 68, 1, 1)
    if not rows:
        oled.text('no satellites', 16, 30, 1)
    for i, (cn, grp, prn, el) in enumerate(rows[:5]):
        y = 13 + 9 * i
        oled.text('{}{:02d}'.format(grp, prn), 0, y, 1)
        oled.text('{:>2}'.format('--' if el is None else el), 36, y, 1)
        _hbar(oled, 60, y, 44, 8, cn * 100 // 50)
        oled.vline(60 + 44 * 70 // 100, y + 6, 2, 1)       # the 35 dB-Hz tick on the bottom edge
        oled.text(str(cn), 108, y, 1)


def _draw_signal(oled, font, parser, jam):
    """Jamming indicator as a panel: the state as a badge, C/N0 and tracked satellites against their learned
    baselines in two readouts, then the per-constellation means, the module's own verdict and the reasons."""
    if not jam:
        _title(oled, 'JAMMING', badge='off')
        return
    _title(oled, 'JAMMING', badge=jam.state)
    tracked, mean, _ = parser.cn0_stats()
    _readout(oled, font, 0, 'C/N0 dB', str(round(mean)), '/{}'.format(round(jam.base_mean)))
    _readout(oled, font, 66, 'SATS', str(tracked), '/{}'.format(round(jam.base_tracked)))
    reason = ' '.join(JAM_WORDS.get(c, c) for c in jam.reason)
    oled.text(reason[:11] or 'no issue', 0, 42, 1)
    aic = parser.pmtk_acks.get(286)
    oled.text('AIC' + ('?' if aic is None else '+' if aic == 3 else '-'), 96, 42, 1)
    means = {}
    for cn, grp, _, _ in _tracked(parser):
        means.setdefault(grp, []).append(cn)
    oled.text('GP{} BD{} m:{}'.format(*(round(sum(means[g]) / len(means[g])) if g in means else '-'
                                        for g in ('G', 'B')), MODULE_JAM.get(parser.module_jam_status, '?')),
              0, 51, 1)


def _draw_spoof(oled, spoof, info):
    """Spoofing indicator as an annunciator panel: one tile per indicator, lit (inverted) while it counts."""
    if not spoof:
        _title(oled, 'SPOOFING', badge='off')
        return
    _title(oled, 'SPOOFING', badge=spoof.state)
    active = spoof.active_codes()
    for i, (code, name) in enumerate(SPOOF_TILES):
        x, y = i % 4 * 32, 12 + i // 4 * 19
        if code in active:
            oled.fill_rect(x, y, 30, 18, 1)
            color = 0
        else:
            _frame(oled, x, y, 30, 18)
            color = 1
        oled.text(code, x + 7, y + 1, color)
        oled.text(name, x + 3, y + 9, color)
    from spoofing import WARMUP_FIXES
    oled.text('armed' if spoof.armed else 'warm {}/{}'.format(spoof.warm_fixes, WARMUP_FIXES), 0, 51, 1)
    if info:
        left = spoof.latch_remaining_ms(info['now_ms']) // 1000
        if left:
            text = 'latch {}:{:02d}'.format(left // 60, left % 60)
            oled.text(text, 128 - 8 * len(text), 51, 1)


def _baud_line(baud, found):
    """'baud 4800 ok' (module already there), 'b4800<9600' (switched from 9600), 'baud 4800 ?' (not heard)."""
    if found is None:
        return 'baud {} ?'.format(baud)
    if found == baud:
        return 'baud {} ok'.format(baud)
    return 'b{}<{}'.format(baud, found)


HEAP_FULL_KB = 192        # the Pico W's heap is about this big: a full heap bar means nothing is used


def _draw_system(oled, info):
    if not info:
        return
    up = info['uptime_s']
    up_text = 'up {}h{:02d}m'.format(up // 3600, up // 60 % 60)
    version = ('v' + info.get('version', '?'))[:15 - len(up_text)]
    oled.text(up_text, 0, 1, 1)
    oled.text(version, 128 - 8 * len(version), 1, 1)
    oled.hline(0, 10, 128, 1)
    kb = info['heap'] // 1024
    oled.text('heap', 0, 13, 1)
    _hbar(oled, 36, 13, 56, 8, kb * 100 // HEAP_FULL_KB)
    oled.text('{}k'.format(kb), 96, 13, 1)
    oled.text('drop{} inv{}%'.format(_cap(info['dropped'], 999), min(info['inv_pct'], 100)), 0, 23, 1)
    oled.text(_baud_line(info['baud'], info['found']), 0, 33, 1)
    oled.text('fix{}ms {}'.format(info['fix_ms'], info['gnss']), 0, 43, 1)
    errors = (info.get('rerr', 0), info.get('gerr', 0), info.get('stale', 0))
    if any(errors):    # contained failures that are otherwise invisible; the board id returns when all is well
        oled.text('ERR r{} g{} s{}'.format(*(_cap(e, 99) for e in errors)), 0, 53, 1)
    else:
        oled.text(info['uid'][:16], 0, 53, 1)


def _draw_debug(oled, font, parser, jam, spoof):
    """Last sentence and date, the satellites used per system as four counters, the detectors' reasons."""
    oled.text(parser.last_valid_sentence.strip()[:16], 0, 0, 1)
    oled.text(parser.date, 0, 9, 1)
    aic = parser.pmtk_acks.get(286)
    oled.text('AIC' + ('?' if aic is None else '+' if aic == 3 else '-'), 96, 9, 1)
    oled.hline(0, 18, 128, 1)
    for i, (label, value) in enumerate((('GPS', parser.birds_GPS), ('SBS', parser.birds_SBAS),
                                        ('BDS', parser.birds_BD), ('OTH', parser.birds_OTHER))):
        x = i * 32
        _frame(oled, x, 20, 30, 29)
        oled.text(label, x + 3, 22, 1)
        text = str(value)
        Writer.set_textpos(oled, 32, x + (30 - _width(font, text)) // 2)
        font.printstring(text)
    if spoof:
        oled.text('S:' + (spoof.reason[:6] or '-'), 0, 52, 1)
    if jam:
        text = 'J:' + (jam.reason or '-')
        oled.text(text, 128 - 8 * len(text), 52, 1)


def _draw_wifi(oled, font, wifi):
    state, ssid, ip, clients, max_clients, password = wifi if wifi else ('OFF', '', '', 0, 0, '')
    _title(oled, 'WI-FI', badge=state[:7])
    oled.text(ssid[:16], 0, 13, 1)
    oled.text('PW', 0, 27, 1)
    _frame(oled, 18, 22, 110, 18)
    if len(password) <= 8:
        Writer.set_textpos(oled, 24, 22)
        font.printstring(password)
    else:                               # a longer password of your own: the small font fits 13 characters
        oled.text(password[:13], 22, 27, 1)
    oled.text(('IP ' + ip)[:16] if ip else 'IP -', 0, 43, 1)
    oled.text('TCP', 0, 52, 1)
    count = '{}/{}'.format(clients, max_clients)
    oled.text(count, 32, 52, 1)
    start = 32 + 8 * len(count) + 8
    for i in range(min(max_clients, (128 - start) // 12)):        # one square per client slot, filled when taken
        x = start + 12 * i
        if i < clients:
            oled.fill_rect(x, 52, 8, 8, 1)
        else:
            _frame(oled, x, 52, 8, 8)
