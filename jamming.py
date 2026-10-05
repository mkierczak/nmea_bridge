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

INIT, OK, LOW, JAM = 'INIT', 'OK', 'LOW', 'JAM?'
_STATES = (OK, LOW, JAM)


class JamDetector(object):

    def __init__(self, parser):
        self.parser = parser
        self.level = 0                # index into _STATES
        self.reason = ''              # letters: C = C/N0 drop, N = fewer satellites, F = fix lost
        self.base_mean = 0.0
        self.base_tracked = 0.0
        self.samples = 0
        self._seen_version = parser.cn0_version
        self._last_gsv_ms = None
        self._up = 0
        self._down = 0

    @property
    def baseline_valid(self):
        return self.samples >= BASELINE_MIN_SAMPLES

    @property
    def state(self):
        return _STATES[self.level] if self.baseline_valid else INIT

    def label(self):
        """Short text for the main screen; blank until the baseline is trusted."""
        return '' if self.state == INIT else self.state

    def signature(self):
        return (self.state, self.reason, round(self.base_mean), round(self.base_tracked))

    def evaluate(self, now_ms, fix_ok):
        """Call periodically. Only acts once per completed GSV cycle (or when data goes stale)."""
        p = self.parser
        fresh = p.cn0_version != self._seen_version
        if fresh:
            self._seen_version = p.cn0_version
            self._last_gsv_ms = now_ms
        elif (self._last_gsv_ms is None or
              _ticks_diff(now_ms, self._last_gsv_ms) < STALE_MS or not self.baseline_valid):
            return self.state, self.reason
        else:
            self._last_gsv_ms = now_ms  # count a stale period once per STALE_MS

        tracked, mean, _ = p.cn0_stats() if fresh else (0, 0, 0)
        if not self.baseline_valid:
            self._learn(tracked, mean, fix_ok)
            return self.state, self.reason

        c = mean < self.base_mean - CN0_DROP_DB
        n = tracked < TRACKED_DROP_FRACTION * self.base_tracked
        f = (not fix_ok) and p.birds_in_view >= HIGH_VIEW_MIN
        if (c and n) or (f and (c or n)):
            level = 2
        elif c or n or f:
            level = 1
        else:
            level = 0
        m = p.module_jam_status  # $PMTKSPF from the module's own detector: 2 warning, 3 critical
        if m >= 2:
            level = max(level, 1 if m == 2 else 2)
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

        if level == 0 and self.level == 0 and fix_ok and tracked > 0:
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