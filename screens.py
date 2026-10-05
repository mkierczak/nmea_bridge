from writer import Writer
from nav import (PAGE_MAIN, PAGE_STATS, PAGE_SATS, PAGE_SIGNAL, PAGE_SPOOF, PAGE_SYSTEM,
                 PAGE_DEBUG, PAGE_WIFI)

JAM_WORDS = {'C': 'cn0', 'N': 'sats', 'F': 'nofix', 'M': 'module'}
SPOOF_NAMES = {'K1': 'jump', 'T1': 'time', 'K2': 'speed', 'S1': 'flat', 'C1': 'GP/BD',
               'K3': 'alt', 'S2': 'elev', 'S3': 'power'}
MODULE_JAM = {0: '?', 1: 'ok', 2: 'warn', 3: 'CRIT'}


def draw(oled, font_large, screen, parser, stats, dropped, no_fix, jam=None, spoof=None, wifi=None, info=None):
    """Render one screen into the frame buffer (caller calls oled.show())."""
    oled.fill(0)
    if screen == PAGE_MAIN:
        oled.text(parser.get_time_string(), 0, 3, 1)  # UTC
        if jam:
            oled.text(jam.label(), 64, 3, 1)  # '', OK, LOW or JAM?
        if spoof:
            oled.text(spoof.label(), 96, 3, 1)  # '', SPF? or SPF!
        oled.hline(0, 14, 128, 1)
        if no_fix:
            oled.text('NO FIX', 40, 28, 1)
        else:
            Writer.set_textpos(oled, 17, 0)
            font_large.printstring(parser.get_lat_string())
            Writer.set_textpos(oled, 32, 0)
            font_large.printstring(parser.get_lon_string())
        oled.hline(0, 48, 128, 1)
        oled.text(parser.fix_type + ' ' + parser.mode + ' ' +
                  str(parser.birds_in_use) + '/' + str(parser.birds_in_view), 0, 54, 1)
        oled.text(parser.get_dop_string(type='PDOP') + parser.get_dop_string(type='HDOP') +
                  parser.get_dop_string(type='VDOP'), 104, 54, 1)
    elif screen == PAGE_STATS:
        rcv = stats['rcv'] or 1  # avoid division by zero in empty windows
        oled.text('rcv{}/m drop{}'.format(round(stats['rcvpm']), dropped), 0, 0, 1)  # 16 chars max
        for row, (key, last) in enumerate((('val', parser.sentence_last_valid_type),
                                           ('inv', parser.sentence_last_invalid_type),
                                           ('par', parser.sentence_last_parsed_type),
                                           ('ign', parser.sentence_last_ignored_type)), 1):
            oled.text(key + ": " + str(round(stats[key] / rcv * 100)) + '% ' + last, 0, row * 12, 1)
        if jam:
            tracked, mean, _ = parser.cn0_stats()
            oled.text("C/N0 {}/{} n{}/{}".format(round(mean), round(jam.base_mean), tracked,
                                                 round(jam.base_tracked)), 0, 56, 1)
    elif screen == PAGE_WIFI:
        state, ssid, ip, clients, max_clients, password = wifi if wifi else ('OFF', '', '', 0, 0, '')
        oled.text('WiFi: ' + state, 0, 0, 1)
        oled.text(ssid[:16], 0, 10, 1)
        oled.text('PW ' + password[:13], 0, 20, 1)
        oled.text(('IP ' + ip) if ip else 'IP -', 0, 30, 1)
        oled.text('TCP clients {}/{}'.format(clients, max_clients), 0, 40, 1)
        oled.text('UP 3s: toggle', 0, 54, 1)
    elif screen == PAGE_SATS:
        _draw_sats(oled, parser)
    elif screen == PAGE_SIGNAL:
        _draw_signal(oled, parser, jam)
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
            oled.text('S:' + spoof.reason[:10], 48, 44, 1)
        oled.text('OTHER:' + str(parser.birds_OTHER), 0, 54, 1)
        if jam:
            oled.text('why:' + jam.reason, 64, 54, 1)
        oled.hline(0, 63, 128, 1)


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
    rows = _tracked(parser)
    oled.text('G{} B{}  el  dB'.format(sum(1 for r in rows if r[1] == 'G'),
                                          sum(1 for r in rows if r[1] == 'B')), 0, 0, 1)
    if not rows:
        oled.text('no satellites', 0, 24, 1)
    for i, (cn, grp, prn, el) in enumerate(rows[:5]):
        y = 10 + 10 * i
        oled.text('{}{:02d} {}'.format(grp, prn, '--' if el is None else el), 0, y, 1)
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
        oled.text('+{} more'.format(len(codes) - 3), 80, 40, 1)
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
    oled.text('heap {}k free'.format(info['heap'] // 1024), 0, 10, 1)
    oled.text('drop {} inv {}%'.format(info['dropped'], info['inv_pct']), 0, 20, 1)
    oled.text(_baud_line(info['baud'], info['found']), 0, 30, 1)
    oled.text('fix{}ms {}'.format(info['fix_ms'], info['gnss']), 0, 40, 1)
    oled.text(info['uid'][:16], 0, 52, 1)
