from writer import Writer
from nav import (PAGE_MAIN, PAGE_STATS, PAGE_SATS, PAGE_SIGNAL, PAGE_SPOOF, PAGE_SYSTEM,
                 PAGE_DEBUG, PAGE_WIFI, PAGE_SPEED)

JAM_WORDS = {'C': 'cn0', 'N': 'sat', 'F': 'fix', 'M': 'mod'}   # short enough for all four on one line
SPOOF_NAMES = {'K1': 'jump', 'T1': 'time', 'K2': 'speed', 'S1': 'flat', 'C1': 'GP/BD',
               'K3': 'alt', 'S2': 'elev', 'S3': 'power'}
MODULE_JAM = {0: '?', 1: 'ok', 2: 'warn', 3: 'CRIT'}


def banner_text(jam, spoof):
    """Text of the alert banner, or '' when nothing strong is going on (SUSPECT / LOW stay small labels)."""
    spf = bool(spoof and spoof.state == 'ALERT')
    jm = bool(jam and jam.state == 'JAM?')
    if spf and jm:
        return 'SPF! JAM?'
    if spf:
        return ('SPF! ' + ' '.join(spoof.reason[i:i + 2] for i in range(0, len(spoof.reason), 2)))[:16]
    if jm:
        return ('JAM? ' + ' '.join(JAM_WORDS.get(c, c) for c in jam.reason))[:16]
    return ''


def _banner(oled, text, height):
    """Inverted bar across the top: white background, black text."""
    oled.fill_rect(0, 0, 128, height, 1)
    oled.text(text, 0, (height - 8) // 2, 0)


def age_text(seconds):
    """'lost 0:42' / 'lost 1h02m': how long ago the last fix was."""
    if seconds < 3600:
        return 'lost {}:{:02d}'.format(seconds // 60, seconds % 60)
    return 'lost {}h{:02d}m'.format(seconds // 3600, seconds // 60 % 60)


def _page_indicator(oled, page, pages):
    """One segment per page along the bottom edge (two pixels high for the current page)."""
    if not pages or page not in pages:
        return
    width = 128 // len(pages)
    for i, p in enumerate(pages):
        x = i * width
        oled.hline(x, 63, width - 2, 1)
        if p == page:
            oled.hline(x, 62, width - 2, 1)


def _draw_hold(oled, hold):
    """Progress box while UP is held for the Wi-Fi gesture; hold = (percent, wifi will be on)."""
    percent, will_be_on = hold
    oled.fill_rect(0, 34, 128, 28, 0)
    oled.hline(0, 34, 128, 1)
    oled.hline(0, 61, 128, 1)
    label = 'Release now!' if percent >= 100 else 'Hold: Wi-Fi ' + ('on' if will_be_on else 'off')
    oled.text(label, (128 - 8 * len(label)) // 2, 38, 1)
    oled.fill_rect(4, 50, 120, 8, 1)
    oled.fill_rect(5, 51, 118, 6, 0)
    oled.fill_rect(5, 51, 118 * min(percent, 100) // 100, 6, 1)


def _draw_speed(oled, font_large, parser, no_fix, banner):
    if banner:
        _banner(oled, banner, 10)
    else:
        oled.text('SOG kn', 0, 1, 1)
        oled.hline(0, 9, 128, 1)
    sog = parser.sog_kn
    moving = not no_fix and sog is not None
    Writer.set_textpos(oled, 13, 0)
    font_large.printstring('{:.1f}'.format(sog) if moving else '--')
    oled.text('COG deg', 0, 31, 1)
    oled.hline(0, 40, 128, 1)
    cog = parser.cog_deg
    Writer.set_textpos(oled, 44, 0)    # a course over ground is meaningless while (nearly) stationary
    font_large.printstring('{:03d}{}'.format(round(cog) % 360, chr(176)) if moving and sog >= 0.5 and cog is not None
                           else '---')


def draw(oled, font_large, screen, parser, stats, dropped, no_fix, jam=None, spoof=None, wifi=None, info=None,
         ctx=None):
    """Render one screen into the frame buffer (caller calls oled.show()). ctx (optional dict) carries what
    only the controller knows: 'pages' (page indicator), 'fix_age_s' (seconds since the last fix, None if
    never), 'banner' (True to show the alert banner) and 'hold' (Wi-Fi gesture progress, see _draw_hold)."""
    ctx = ctx or {}
    banner = banner_text(jam, spoof) if ctx.get('banner') else ''
    oled.fill(0)
    if screen == PAGE_SPEED:
        _draw_speed(oled, font_large, parser, no_fix, banner)
    elif screen == PAGE_MAIN:
        if banner:
            _banner(oled, banner, 13)
        else:
            oled.text(parser.get_time_string(), 0, 3, 1)  # UTC
            jam_label = jam.label() if jam else ''        # '', OK, LOW or JAM?
            spoof_label = spoof.label() if spoof else ''  # '', SPF? or SPF!
            if jam_label:
                oled.text(jam_label, 72, 3, 1)            # one character of space after the time
            if spoof_label:
                if 72 + 8 * len(jam_label) >= 128 - 8 * len(spoof_label):
                    spoof_label = 'S' + spoof_label[-1]   # both at once: 'S?' / 'S!' so they never touch
                oled.text(spoof_label, 128 - 8 * len(spoof_label), 3, 1)
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
    elif screen == PAGE_STATS:
        rcv = stats['rcv'] or 1  # avoid division by zero in empty windows
        oled.text('rx{}/m d{}'.format(_cap(round(stats['rcvpm']), 9999), _cap(dropped, 9999)), 0, 0, 1)
        for row, (key, last) in enumerate((('val', parser.sentence_last_valid_type),
                                           ('inv', parser.sentence_last_invalid_type),
                                           ('par', parser.sentence_last_parsed_type),
                                           ('ign', parser.sentence_last_ignored_type)), 1):
            oled.text(key + ": " + str(round(stats[key] / rcv * 100)) + '% ' + last, 0, row * 10, 1)
        if jam:
            tracked, mean, _ = parser.cn0_stats()
            oled.text('CN {}/{} n{}/{}'.format(round(mean), round(jam.base_mean), tracked,
                                               round(jam.base_tracked)), 0, 54, 1)   # 16 chars at most
    elif screen == PAGE_WIFI:
        state, ssid, ip, clients, max_clients, password = wifi if wifi else ('OFF', '', '', 0, 0, '')
        oled.text('WiFi: ' + state, 0, 0, 1)
        oled.text(ssid[:16], 0, 10, 1)
        oled.text('PW ' + password[:13], 0, 20, 1)
        oled.text(('IP ' + ip)[:16] if ip else 'IP -', 0, 30, 1)
        oled.text('TCP clients {}/{}'.format(clients, max_clients)[:16], 0, 40, 1)
        oled.text('UP 3s: toggle', 0, 54, 1)
    elif screen == PAGE_SATS:
        _draw_sats(oled, parser)
    elif screen == PAGE_SIGNAL:
        _draw_signal(oled, parser, jam)
    _page_indicator(oled, screen, ctx.get('pages'))
    if ctx.get('hold'):
        _draw_hold(oled, ctx['hold'])
    elif screen == PAGE_SPOOF:
        _draw_spoof(oled, spoof, info)
    elif screen == PAGE_SYSTEM:
        _draw_system(oled, info)
    elif screen == PAGE_DEBUG:
        oled.text(parser.last_valid_sentence.strip()[:16], 0, 0, 1)
        oled.text(parser.date, 0, 10, 1)
        aic = parser.pmtk_acks.get(286)
        oled.text('AIC' + ('?' if aic is None else '+' if aic == 3 else '-'), 96, 10, 1)
        oled.hline(0, 20, 128, 1)
        oled.text('GPS:' + str(parser.birds_GPS), 0, 24, 1)
        oled.text('SBAS:' + str(parser.birds_SBAS), 0, 34, 1)
        oled.text('BD:' + str(parser.birds_BD), 0, 44, 1)
        if spoof:
            oled.text('S:' + spoof.reason[:8], 48, 44, 1)    # 10 characters from x=48 end at the edge
        oled.text('OTHER:' + str(parser.birds_OTHER), 0, 54, 1)
        if jam:
            oled.text('why:' + jam.reason, 64, 54, 1)
        oled.hline(0, 63, 128, 1)


def _cap(value, limit):
    """Clip a counter for display: '9999+' instead of a number too wide for its line."""
    return str(value) if value <= limit else '{}+'.format(limit)


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
    """Column labels (aligned with the data below), a rule, then the five strongest satellites:
    id (G = GPS, B = BeiDou + PRN), elevation, a C/N0 bar and the C/N0 value in dB-Hz."""
    rows = _tracked(parser)
    oled.text('sat  el C/N0', 0, 0, 1)
    oled.hline(0, 9, 128, 1)
    if not rows:
        oled.text('no satellites', 0, 24, 1)
    for i, (cn, grp, prn, el) in enumerate(rows[:5]):
        y = 12 + 10 * i
        oled.text('{:<4}{:>3}'.format('{}{:02d}'.format(grp, prn), '--' if el is None else el), 0, y, 1)
        oled.fill_rect(64, y, min(36, cn * 36 // 50), 7, 1)
        oled.text(str(cn), 104, y, 1)


def _draw_signal(oled, parser, jam):
    if not jam:
        oled.text('Jamming: off', 0, 0, 1)
        return
    oled.text('JAM ' + jam.state, 0, 0, 1)
    oled.text(' '.join(JAM_WORDS.get(c, c) for c in jam.reason)[:16] or 'no issue', 0, 10, 1)
    tracked, mean, _ = parser.cn0_stats()
    oled.text('CN0 {}/{} dB'.format(round(mean), round(jam.base_mean)), 0, 20, 1)
    oled.text('sats {}/{}'.format(tracked, round(jam.base_tracked)), 0, 30, 1)
    means = {}
    for cn, grp, _, _ in _tracked(parser):
        means.setdefault(grp, []).append(cn)
    oled.text('GP{} BD{} dB'.format(*(round(sum(means[g]) / len(means[g])) if g in means else '-'
                                      for g in ('G', 'B'))), 0, 40, 1)
    aic = parser.pmtk_acks.get(286)
    oled.text('mod:{} AIC{}'.format(MODULE_JAM.get(parser.module_jam_status, '?'),
                                    '?' if aic is None else '+' if aic == 3 else '-'), 0, 50, 1)


def _draw_spoof(oled, spoof, info):
    if not spoof:
        oled.text('Spoofing: off', 0, 0, 1)
        return
    oled.text('SPF ' + spoof.state, 0, 0, 1)
    from spoofing import WARMUP_FIXES
    oled.text('armed' if spoof.armed else 'warm-up {}/{}'.format(spoof.warm_fixes, WARMUP_FIXES), 0, 10, 1)
    codes = spoof.active_codes()
    for i, code in enumerate(codes[:3]):
        oled.text('{} {}'.format(code, SPOOF_NAMES.get(code, '')), 0, 20 + 10 * i, 1)
    if len(codes) > 3:
        oled.text('+{} more'.format(len(codes) - 3), 64, 40, 1)
    if not codes:
        oled.text('no indicators', 0, 20, 1)
    if info:
        left = spoof.latch_remaining_ms(info['now_ms']) // 1000
        if left:
            oled.text('latch {}:{:02d}'.format(left // 60, left % 60), 0, 52, 1)


def _baud_line(baud, found):
    """'baud 4800 ok' (module already there), 'b4800<9600' (switched from 9600), 'baud 4800 ?' (not heard)."""
    if found is None:
        return 'baud {} ?'.format(baud)
    if found == baud:
        return 'baud {} ok'.format(baud)
    return 'b{}<{}'.format(baud, found)


def _draw_system(oled, info):
    if not info:
        return
    up = info['uptime_s']
    oled.text('up {}h{:02d}m{:02d}s'.format(up // 3600, up // 60 % 60, up % 60), 0, 0, 1)
    oled.text('heap {}k free'.format(info['heap'] // 1024), 0, 9, 1)
    oled.text('drop{} inv{}%'.format(_cap(info['dropped'], 999), min(info['inv_pct'], 100)), 0, 18, 1)
    oled.text(_baud_line(info['baud'], info['found']), 0, 27, 1)
    oled.text('fix{}ms {}'.format(info['fix_ms'], info['gnss']), 0, 36, 1)
    oled.text(('v' + info.get('version', '?'))[:16], 0, 45, 1)
    errors = (info.get('rerr', 0), info.get('gerr', 0), info.get('stale', 0))
    if any(errors):    # contained failures that are otherwise invisible; the board id returns when all is well
        oled.text('ERR r{} g{} s{}'.format(*(_cap(e, 99) for e in errors)), 0, 54, 1)
    else:
        oled.text(info['uid'][:16], 0, 54, 1)
