"""Replay a recorded NMEA log through the same radio-path code the board runs, to tune the detectors.

    python3 tools/replay.py LOG [--baud 4800] [--gnss gps+bd|gps] [--set KEY=VALUE ...] [--no-jam] [--no-spoof] [-q]

LOG has one sentence per line, optionally prefixed with the local arrival time in milliseconds
("123456 $GNRMC,..."): exactly what the menu's "Log raw" option prints, so
`mpremote repl | tee log.txt` gives a replayable file. Other lines (REPL noise) are ignored. Without
timestamps the arrival time is synthesised from the GPS time of the RMC sentences (which makes the
GPS-time check T1 meaningless, since both clocks are the same).

The replay drives a real bridge.Bridge, so evaluation cadence, GSV settling, the T1 tolerance for the
GPS link rate (--baud/--gnss) and the detectors behave as on the board. --set overrides the advanced
thresholds from the menu (for example --set cn0_drop_db=8 --set max_speed_kn=40). The output lists every
state change and ends with a summary: time in each state and alarm episodes per hour, i.e. the false-alarm
rate when the log was recorded in normal conditions.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

import NMEA                                # noqa: E402
import jamming                             # noqa: E402
import linkcalc                            # noqa: E402
import settings                            # noqa: E402
import spoofing                            # noqa: E402
from bridge import Bridge, RxQueue         # noqa: E402

JAM_STATES = ('INIT', 'OK', 'LOW', 'JAM?')
JAM_ALARMS = ('LOW', 'JAM?')
SPOOF_STATES = ('OK', 'SUSPECT', 'ALERT')
SPOOF_ALARMS = ('SUSPECT', 'ALERT')


class NullRadio(object):
    def write(self, text):
        pass


class Clock(object):
    now = 0

    def __call__(self):
        return self.now


def parse_log(lines):
    """[(ms or None, sentence)] for every sentence line; anything else is skipped."""
    entries = []
    for line in lines:
        line = line.strip()
        head, _, rest = line.partition(' ')
        if head.isdigit() and rest.startswith('$'):
            entries.append((int(head), rest))
        elif line.startswith('$'):
            entries.append((None, line))
    return entries


def assign_times(entries):
    """Give every entry an arrival time in ms. Timestamps are used as they are; if there are none the time is
    taken from the GPS time of the most recent RMC; a mixed log reuses the previous entry's time."""
    if any(ms is not None for ms, _ in entries):
        out, last = [], 0
        for ms, sentence in entries:
            last = ms if ms is not None else last
            out.append((last, sentence))
        return out
    scratch = NMEA.Parser()
    origin = None
    current = 0
    out = []
    for _, sentence in entries:
        before = scratch.fix_count
        scratch.parse_sentence(sentence)
        if scratch.fix_count != before:
            gps_ms = scratch.utc_days * 86400000 + scratch.utc_ms
            if origin is None:
                origin = gps_ms
            current = gps_ms - origin
        out.append((current, sentence))
    return out


def parse_overrides(pairs):
    """['key=value', ...] -> validated {key: int} for the advanced threshold settings."""
    allowed = settings.threshold_defaults(jamming, spoofing)
    out = {}
    for pair in pairs or []:
        key, sep, value = pair.partition('=')
        if not sep or key not in allowed:
            raise ValueError('unknown threshold {!r}; choose from: {}'.format(key, ', '.join(sorted(allowed))))
        try:
            out[key] = settings.coerce(key, int(value))
        except ValueError:
            raise ValueError('bad value {!r} for {}'.format(value, key))
    return out


class _Cfg(object):
    def __init__(self, values):
        self.values = values

    def get(self, key):
        return self.values[key]


IDLE_STEP_MS = 250        # the board's loop runs every ~10 ms; between sentences we step this often ...
IDLE_FAST_MS = 3000       # ... for this long after a sentence (GSV settling, expiry), then once a second
IDLE_SLOW_MS = 1000


def replay(entries, baud=4800, gnss='GPS+BD', overrides=None, jam=True, spoof=True):
    """Run the entries through a Bridge, stepping it between sentences like the board's loop does.

    Returns a dict: transitions, seconds in each state, alarm episodes, counts of sentences."""
    entries = assign_times(entries)
    original = settings.threshold_defaults(jamming, spoofing)
    values = dict(original)
    values.update(overrides or {})
    settings.apply_thresholds(_Cfg(values), jamming, spoofing)
    try:
        clock = Clock()
        parser = NMEA.Parser()
        queue = RxQueue(1 << 30)
        bridge = Bridge(parser, queue, NullRadio(), clock)
        if jam:
            bridge.detector = jamming.JamDetector(parser)
        if spoof:
            tolerance = spoofing.TIME_JUMP_MS + linkcalc.nmea_burst_ms(baud, gnss.upper() == 'GPS+BD')
            bridge.spoof = spoofing.SpoofDetector(parser, tolerance)
        transitions = []
        seconds = {'jam': {s: 0.0 for s in JAM_STATES}, 'spoof': {s: 0.0 for s in SPOOF_STATES}}
        episodes = {'jam': 0, 'spoof': 0}
        current = {'jam': ('INIT', ''), 'spoof': ('OK', '')}
        t0 = entries[0][0] if entries else 0
        acct = [t0]                               # time up to which seconds[] has been accounted

        def observe(ms):
            dt = (ms - acct[0]) / 1000.0
            acct[0] = ms
            seconds['jam'][current['jam'][0]] += dt
            seconds['spoof'][current['spoof'][0]] += dt
            now_states = {'jam': (bridge.detector.state, bridge.detector.reason) if jam else current['jam'],
                          'spoof': (bridge.spoof.state, bridge.spoof.reason) if spoof else current['spoof']}
            for kind, alarms in (('jam', JAM_ALARMS), ('spoof', SPOOF_ALARMS)):
                if now_states[kind] != current[kind]:
                    if now_states[kind][0] in alarms and current[kind][0] not in alarms:
                        episodes[kind] += 1
                    transitions.append(((ms - t0) / 1000.0, kind, now_states[kind][0], now_states[kind][1],
                                        parser.cn0_stats()))
                    current[kind] = now_states[kind]

        def step(ms):
            clock.now = ms
            bridge.step(ms)
            observe(ms)

        last_ms = t0
        for ms, sentence in entries:
            while last_ms < ms:                   # idle steps, as the board's loop would take them
                gap = ms - last_ms
                last_ms += min(gap, IDLE_STEP_MS if ms - last_ms <= IDLE_FAST_MS else IDLE_SLOW_MS)
                step(last_ms)
            queue.push((ms, sentence))
            step(ms)
        # the bridge resets the parser's counters every 10 s for its statistics window, so count separately
        counts = NMEA.Parser()
        for _, sentence in entries:
            counts.parse_sentence(sentence)
        return {'transitions': transitions, 'seconds': seconds, 'episodes': episodes,
                'duration_s': (last_ms - t0) / 1000.0, 'jam_detector': bridge.detector,
                'counts': {'received': counts.sentences_received, 'valid': counts.sentences_valid,
                           'invalid': counts.sentences_invalid, 'unreadable': counts.parse_errors}}
    finally:
        settings.apply_thresholds(_Cfg(original), jamming, spoofing)   # leave the modules as we found them


def format_transition(t, jam_detector=None):
    when, kind, state, reason, cn0 = t
    if kind == 'jam':
        base = ' base={:.1f}'.format(jam_detector.base_mean) if jam_detector else ''
        return '{:9.1f}s JAM   {:5} why={:4} cn0={:4.1f}{} n={}'.format(when, state, reason, cn0[1], base, cn0[0])
    return '{:9.1f}s SPOOF {:7} {}'.format(when, state, reason)


def format_summary(result):
    c = result['counts']
    duration = result['duration_s']
    hours = duration / 3600.0
    lines = ['--- summary ---',
             'log duration   {:.0f} s ({}h{:02d}m), {} sentences: {} valid, {} invalid, {} with unreadable fields'.format(
                 duration, int(duration // 3600), int(duration // 60 % 60),
                 c['received'], c['valid'], c['invalid'], c['unreadable'])]
    for kind, label, states in (('jam', 'jamming', JAM_STATES), ('spoof', 'spoofing', SPOOF_STATES)):
        secs = result['seconds'][kind]
        total = sum(secs.values()) or 1.0
        shares = '  '.join('{} {:.1f}%'.format(s, 100.0 * secs[s] / total) for s in states)
        per_hour = result['episodes'][kind] / hours if hours > 0 else 0.0
        lines.append('{:<14} {}'.format(label, shares))
        lines.append('{:<14} alarm episodes: {} ({:.2f} per hour)'.format('', result['episodes'][kind], per_hour))
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Replay a recorded NMEA log through the bridge and its detectors.')
    ap.add_argument('log')
    ap.add_argument('--baud', type=int, default=4800, help='GPS link rate (sets the T1 tolerance), default 4800')
    ap.add_argument('--gnss', choices=('gps', 'gps+bd'), default='gps+bd', help='GNSS mode, default gps+bd')
    ap.add_argument('--set', action='append', metavar='KEY=VALUE', help='advanced threshold override')
    ap.add_argument('--no-jam', action='store_true')
    ap.add_argument('--no-spoof', action='store_true')
    ap.add_argument('-q', '--quiet', action='store_true', help='print only the summary')
    args = ap.parse_args(argv)
    try:
        overrides = parse_overrides(args.set)
    except ValueError as e:
        ap.error(str(e))
    with open(args.log) as f:
        entries = parse_log(f)
    if not entries:
        ap.error('no NMEA sentences found in ' + args.log)
    result = replay(entries, args.baud, args.gnss, overrides, jam=not args.no_jam, spoof=not args.no_spoof)
    if not args.quiet:
        for t in result['transitions']:
            print(format_transition(t, result['jam_detector']))
    print(format_summary(result))


if __name__ == '__main__':
    main()
