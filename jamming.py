"""GPS signal-degradation / jamming indicator based on GSV C/N0 and fix status.

NMEA only exposes signal quality, so obstruction, indoor use or an antenna fault look the
same as jamming. States therefore say "signal low" / "jamming possible", never "confirmed".
No hardware imports: runs on MicroPython and CPython (tests, tools/replay.py).
"""

try:
    from utime import ticks_diff as _ticks_diff   # ticks_ms() wraps (about every 12.4 days)
except ImportError:
    def _ticks_diff(a, b):
        return a - b

# Thresholds (tune with tools/replay.py on recorded logs)
CN0_DROP_DB = 6              # mean C/N0 this far below baseline => indicator C
TRACKED_DROP_FRACTION = 0.6  # tracked satellites below this fraction of baseline => indicator N
HIGH_VIEW_MIN = 6            # satellites in view that make a lost fix suspicious => indicator F
BASELINE_MIN_SAMPLES = 5     # good GSV cycles before the baseline is trusted
BASELINE_ALPHA = 0.05        # EMA weight of a new good sample
ENTER_CYCLES = 2             # consecutive worse cycles before the state worsens
EXIT_CYCLES = 3              # consecutive better cycles before the state improves
STALE_MS = 20 * 1000         # no complete GSV cycle for this long counts as zero tracked

INIT, OK, LOW, MEDIUM, HIGH = 'INIT', 'OK', 'LOW', 'MEDIUM', 'HIGH'   # INIT: no baseline yet, no verdict
_STATES = (OK, LOW, MEDIUM, HIGH)       # the probability of jamming; MEDIUM and HIGH are alerts
_MARKS = {LOW: '.', MEDIUM: '?', HIGH: '!'}


class JamDetector(object):

    def __init__(self, parser):
        self.parser = parser
        self.level = 0                # index into _STATES
        self.reason = ''              # letters: C = C/N0 drop, N = fewer satellites, F = fix lost
        self.base_mean = 0.0
        self.base_tracked = 0.0
        self.samples = 0
        self._seen_version = parser.cn0_version
        self._seen_spf = parser.spf_version
        self._last_gsv_ms = None
        self._up = 0
        self._down = 0

    @property
    def baseline_valid(self):
        return self.samples >= BASELINE_MIN_SAMPLES

    @property
    def state(self):
        if self.baseline_valid:
            return _STATES[self.level]
        # before the baseline exists only F (no fix, sky full of satellites) and M (module) can speak
        return _STATES[self.level] if self.level > 0 else INIT

    def label(self):
        """Short text for the main screen: 'JAM.' (low), 'JAM?' (medium), 'JAM!' (high); blank while OK or
        until the baseline is trusted."""
        mark = _MARKS.get(self.state)
        return 'JAM' + mark if mark else ''

    def signature(self):
        return (self.state, self.reason, round(self.base_mean), round(self.base_tracked))

    def evaluate(self, now_ms, fix_ok):
        """Call periodically. Acts once per settled GSV cycle, on a new module status, or when data
        goes stale; otherwise returns the current state unchanged."""
        p = self.parser
        fresh = p.cn0_version != self._seen_version and p.cn0_settled(now_ms)
        module_new = p.spf_version != self._seen_spf
        stale = False
        if fresh:
            self._seen_version = p.cn0_version
            self._last_gsv_ms = now_ms
        elif not module_new:
            if (self._last_gsv_ms is None or _ticks_diff(now_ms, self._last_gsv_ms) < STALE_MS
                    or not self.baseline_valid):
                return self.state, self.reason
            self._last_gsv_ms = now_ms  # count a stale period once per STALE_MS
            stale = True
        self._seen_spf = p.spf_version

        tracked, mean, _ = (0, 0, 0) if stale else p.cn0_stats()
        f = (not fix_ok) and p.birds_in_view >= HIGH_VIEW_MIN
        m = p.module_jam_status  # $PMTKSPF from the module's own detector: 2 warning, 3 critical
        if self.baseline_valid:
            c = mean < self.base_mean - CN0_DROP_DB
            n = tracked < TRACKED_DROP_FRACTION * self.base_tracked
        else:
            c = n = False  # nothing to compare with yet
        # the probability grows with the number of agreeing kinds of evidence; the module's own warning
        # counts once, its critical status twice: 1 = LOW, 2 = MEDIUM, 3 or more = HIGH
        level = min(3, bool(c) + bool(n) + bool(f) + (0 if m < 2 else 1 if m == 2 else 2))
        self.reason = ('C' if c else '') + ('N' if n else '') + ('F' if f else '') + ('M' if m >= 2 else '')

        if level > self.level:
            self._up += 1
            self._down = 0
            if self._up >= ENTER_CYCLES:
                self.level, self._up = level, 0
        elif level < self.level:
            self._down += 1
            self._up = 0
            if self._down >= EXIT_CYCLES:
                self.level, self._down = level, 0
        else:
            self._up = self._down = 0

        if self.baseline_valid:
            if level == 0 and self.level == 0 and fix_ok and tracked > 0:
                self._learn(tracked, mean, fix_ok)
        elif not f and m < 2:  # never learn "normal" from a sample that already looks like trouble
            self._learn(tracked, mean, fix_ok)
        return self.state, self.reason

    def _learn(self, tracked, mean, fix_ok):
        if not fix_ok or tracked == 0:
            return
        if self.samples == 0:
            self.base_mean, self.base_tracked = mean, float(tracked)
        else:
            a = BASELINE_ALPHA if self.baseline_valid else 1.0 / (self.samples + 1)
            self.base_mean += a * (mean - self.base_mean)
            self.base_tracked += a * (tracked - self.base_tracked)
        self.samples += 1