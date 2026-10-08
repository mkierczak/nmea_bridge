"""The radio path of the bridge, free of hardware imports so it can be tested on a desktop.

GPS bytes -> SentenceFramer -> RxQueue -> Bridge.step() -> radio UART (and Wi-Fi), with detectors,
statistics and a watchdog policy. Everything that touches hardware (UART, WDT, reset) is injected.

Design rule: forwarding sentences to the radio is the one job that must never stop. Everything else
(detectors, Wi-Fi, logging, statistics) runs inside a guard that counts failures and switches the failing
part off for a while instead of letting an exception take the loop down.
"""

import alerts
import units

try:
    from utime import ticks_diff
except ImportError:
    def ticks_diff(a, b):
        return a - b

MAX_SENTENCE_LEN = 100        # longer buffers are garbage; resync on the next '$'
MAX_AGE_MS = 3000             # position sentences older than this (after a stall) are not forwarded
POSITION_TYPES = ('RMC', 'GGA')
ALERT_STATES = ('MEDIUM', 'HIGH')   # the probability levels of the detectors that count as an alert
SPOOF_TYPES = ('RMC', 'GGA')    # GSV statistics are picked up at the next fix or by the periodic evaluation
STATS_PERIOD_MS = 10 * 1000
JAM_EVAL_PERIOD_MS = 2 * 1000
SPOOF_IDLE_PERIOD_MS = 1000
WIFI_POLL_PERIOD_MS = 200
FIX_STALE_TIMEOUT_MS = 10 * 1000
GPS_SILENCE_TIMEOUT_MS = 30 * 1000
FATAL_GRACE_MS = 5 * 1000     # GPS thread dead before the watchdog is armed: reset after this long
STRIKES_TO_DISABLE = 3        # consecutive failures of an optional part ...
DISABLE_MS = 30 * 1000        # ... switch it off for this long
CRITICAL_FAILURES_MAX = 20    # consecutive unexpected failures in the sentence path => on_fatal
REPROBE_AFTER_MS = 30 * 1000  # no valid sentence for this long: look for the GPS module again
REPROBE_EVERY_MS = 60 * 1000
HEARTBEAT_PULSE_MS = 350      # the heartbeat shows 'on' this long after a position sentence reached the radio
HEARTBEAT_IDLE_MS = 3000      # nothing forwarded for this long: 'idle'
HEARTBEAT_FAULT_MS = 10 * 1000   # a radio write failure or a stale drop shows as 'fault' for this long
FORWARD_CACHE_MAX = 64        # distinct sentence types remembered (valid checksums only, so this is generous)
_TO_RADIO, _TO_WIFI = 1, 2


class NullLock(object):
    def acquire(self):
        pass

    def release(self):
        pass


class SentenceFramer(object):
    """Cuts a byte stream into NMEA lines. A line ends at LF, or when the next '$' starts.

    Uses one preallocated buffer (no per-byte allocation). Bytes outside 10..126 are dropped; a line
    longer than max_len is truncated (its checksum then fails downstream)."""

    def __init__(self, max_len=MAX_SENTENCE_LEN):
        self._buf = bytearray(max_len)
        self._view = memoryview(self._buf)
        self._max = max_len
        self._n = 0

    def _take(self, out):
        out.append(bytes(self._view[0:self._n]).decode())
        self._n = 0

    def feed(self, data):
        """Returns the list of complete lines (str) found in 'data'."""
        out = []
        buf = self._buf
        for ch in data:
            if not 10 <= ch <= 126:
                continue
            if ch == 0x24 and self._n:       # '$': the previous line had no terminator
                self._take(out)
            if self._n < self._max:
                buf[self._n] = ch
                self._n += 1
            if ch == 10 and self._n:         # end of line: hand it over at once (accurate arrival time)
                self._take(out)
        return out


class RxQueue(object):
    """Bounded queue between the GPS thread and the main loop; drops the oldest when full."""

    def __init__(self, maxlen=16, lock=None):
        self.maxlen = maxlen
        self.dropped = 0
        self._items = []
        self._lock = lock or NullLock()

    def push(self, item):
        self._lock.acquire()
        try:
            if len(self._items) >= self.maxlen:
                self._items.pop(0)
                self.dropped += 1
            self._items.append(item)
        finally:
            self._lock.release()

    def drain(self):
        self._lock.acquire()
        try:
            items = self._items[:]
            del self._items[:]
        finally:
            self._lock.release()
        return items


def forward_decision(sentence_type, talker, forward_types, forward_talkers, wifi_talkers,
                     block, block_types, wifi_on):
    """(send to the radio?, send over Wi-Fi?). While 'block' is set (spoofing alert in block mode) the
    position sentences in block_types go nowhere."""
    if block and sentence_type in block_types:
        return False, False
    wanted = sentence_type in forward_types
    return wanted and talker in forward_talkers, wifi_on and wanted and talker in wifi_talkers


class WatchdogPolicy(object):
    """When to arm and feed the hardware watchdog.

    It arms on the first checksum-valid sentence (garbage never arms it, a bench without GPS never
    reboots) and is fed only while valid sentences keep arriving and the GPS thread is healthy, so a
    dead or deaf GPS path ends in a reset instead of a silent radio."""

    def __init__(self, silence_ms=GPS_SILENCE_TIMEOUT_MS):
        self.silence_ms = silence_ms
        self.armed = False
        self.fatal = False
        self.last_valid = None

    def on_valid(self, now):
        self.last_valid = now

    def should_arm(self):
        return not self.armed and self.last_valid is not None

    def should_feed(self, now):
        return (self.armed and not self.fatal and self.last_valid is not None
                and ticks_diff(now, self.last_valid) < self.silence_ms)


class GpsReader(object):
    """Reads the GPS UART, frames sentences and queues them with their arrival time.

    init_fn() configures the module (baud probing and commands) and returns the baud rate the module
    was found at. If nothing valid arrives for REPROBE_AFTER_MS the module is looked for again (it may
    have been power-cycled and fallen back to 9600 baud with its default configuration)."""

    def __init__(self, gps, queue, clock, init_fn):
        self.gps = gps
        self.queue = queue
        self.clock = clock
        self.init_fn = init_fn
        self.framer = SentenceFramer()
        self.found = None
        self.fatal = False           # True while the thread is failing and being restarted
        self.errors = 0
        self.last_good = None
        self._last_probe = None

    def start(self):
        self.found = self.init_fn()
        self.last_good = self.clock()
        self._last_probe = None      # init_fn just ran; a later re-probe is only needed after silence
        self.fatal = False

    def step(self):
        """One read cycle. Returns True if bytes were handled."""
        n = self.gps.uart_any()
        if not n:
            return False
        data = self.gps.uart_receive_string(n)
        if not data:
            return False
        now = self.clock()
        for sentence in self.framer.feed(data):
            self.queue.push((now, sentence))
        return True

    def maintain(self):
        """Re-probe the module when it has gone quiet or unintelligible."""
        now = self.clock()
        if (ticks_diff(now, self.last_good) > REPROBE_AFTER_MS
                and (self._last_probe is None or ticks_diff(now, self._last_probe) > REPROBE_EVERY_MS)):
            self._last_probe = now
            self.found = self.init_fn()
            self.last_good = self.clock()

    def run_forever(self, sleep_ms, report=None):
        """Thread body: never returns; any exception is reported and the reader restarted."""
        while True:
            try:
                self.start()
                while True:
                    if not self.step():
                        sleep_ms(2)
                    self.maintain()
            except Exception as e:
                self.errors += 1
                self.fatal = True
                if report:
                    report('gps thread', e)
                sleep_ms(1000)


class RawLogger(object):
    """Prints '<arrival ms> <sentence>' for every framed sentence (all talkers, as received): the format
    tools/replay.py reads. If a print takes longer than max_ms (a USB console that is attached but not being
    read blocks writes) it switches itself off and calls on_disable, so logging can never stall the loop."""

    def __init__(self, write, clock, max_ms=200, on_disable=None):
        self.write = write
        self.clock = clock
        self.max_ms = max_ms
        self.on_disable = on_disable
        self.disabled = False

    def __call__(self, rx_ms, sentence):
        if self.disabled:
            return
        start = self.clock()
        self.write('{} {}'.format(rx_ms, sentence.strip()))
        if ticks_diff(self.clock(), start) > self.max_ms:
            self.disabled = True
            if self.on_disable:
                self.on_disable()


class Bridge(object):
    """Sentences in, radio/Wi-Fi/detectors/statistics out. Call step(now) from the main loop."""

    def __init__(self, parser, queue, radio, clock, on_fatal=None, log=None):
        self.parser = parser
        self.queue = queue
        self.radio = radio                 # anything with write(str)
        self.clock = clock
        self.on_fatal = on_fatal
        self.log = log                     # callable(str) for acks and failures, or None
        self.report = None                 # callable(name, exception) for failures, or None
        self.on_forward = None             # callable(sentence) for debug output
        self.on_raw = None                 # callable(rx_ms, sentence) for raw logging
        self.forward_types = ('RMC', 'GGA', 'GSA', 'GSV', 'ZDA')
        self.forward_talkers = ('GP', 'GN')
        self.wifi_talkers = ('GP', 'GN', 'BD')
        self.block_types = ('RMC', 'GGA')
        self.spoof_action = 'display'
        self.detector = None               # jamming.JamDetector
        self.spoof = None                  # spoofing.SpoofDetector
        self.broadcaster = None            # wifi.NmeaBroadcaster
        self.reader = None                 # GpsReader (for failure monitoring)
        self.watchdog = WatchdogPolicy()
        self.stats = {'rcvpm': 1, 'rcv': 1, 'val': 0, 'inv': 0, 'par': 0, 'ign': 0}
        self.last_pos = None
        self.last_fix = None               # when the last valid fix (RMC status A) arrived
        self.last_forward = None           # when the radio last took a position sentence
        self.last_fault = None             # when a radio write failed or a late position sentence was dropped
        self._fix_seen = 0
        self.stale_dropped = 0
        self.radio_errors = 0
        self.errors = {}                   # guard name -> failure count
        self.last_error = None             # (name, repr)
        self._strikes = {}
        self._disabled_at = {}
        self._critical = 0
        self._wifi_lines = []
        self.alert_log = alerts.AlertLog()
        self._logged = {}                  # detector tag -> the level (0 none, 1 MEDIUM, 2 HIGH) already logged
        self._fwd_cfg = [None, None, None, None]   # the lists the cache below was computed for
        self._fwd_cache = {}                       # sentence type -> {talker -> forward bits}
        self._logged_acks = {}
        self._fatal_since = None
        now = clock()
        self._last_stats = self._last_jam = self._last_spoof = self._last_wifi_poll = now

    # --- state used by the UI ---------------------------------------------------------------
    def no_fix(self, now):
        p = self.parser
        return (p.fix_type == 'NO' or self.last_pos is None
                or ticks_diff(now, self.last_pos) > FIX_STALE_TIMEOUT_MS)

    def fix_age_ms(self, now):
        """Milliseconds since the last valid fix, or None if there has not been one since boot."""
        return None if self.last_fix is None else ticks_diff(now, self.last_fix)

    def heartbeat(self, now):
        """State of forwarding for the Main page: 'on' / 'off' alternate while position sentences reach the
        radio, 'idle' when none did lately (no data, no fix, types switched off, blocked), 'fault' after a
        radio write failure or a position sentence dropped for being late."""
        if self.last_fault is not None and ticks_diff(now, self.last_fault) < HEARTBEAT_FAULT_MS:
            return 'fault'
        if self.last_forward is None:
            return 'idle'
        age = ticks_diff(now, self.last_forward)
        if age >= HEARTBEAT_IDLE_MS:
            return 'idle'
        return 'on' if age < HEARTBEAT_PULSE_MS else 'off'

    def guard_errors(self):
        """Failures contained in the optional parts (everything but the radio), since boot."""
        return sum(count for name, count in self.errors.items() if name != 'radio')

    def alert(self):
        """True while a jamming or spoofing alert (MEDIUM or HIGH probability) should keep the display on."""
        return bool((self.spoof and self.spoof.state in ALERT_STATES) or
                    (self.detector and self.detector.state in ALERT_STATES))

    # --- the loop body ---------------------------------------------------------------------
    def step(self, now):
        for rx_ms, sentence in self.queue.drain():
            self._handle_sentence(now, rx_ms, sentence)
        self._flush_wifi(now)
        self._periodic(now)
        self._check_reader(now)

    def _handle_sentence(self, now, rx_ms, sentence):
        if self.on_raw:
            self._guard('log_raw', self.on_raw, rx_ms, sentence)
        try:
            ok = self.parser.parse_sentence(sentence, rx_ms)
        except Exception as e:             # a parser bug must not end the loop, nor forward unchecked data
            self._critical_failure('parse', e)
            return
        self._critical = 0
        if not ok:
            return
        self.watchdog.on_valid(now)
        if self.reader is not None:
            self.reader.last_good = now    # the GPS thread re-probes the module when this goes stale
        p = self.parser
        sentence_type = p.sentence_last_valid_type
        if sentence_type in POSITION_TYPES:
            self.last_pos = now
        if p.fix_count != self._fix_seen:
            self._fix_seen = p.fix_count
            self.last_fix = now
        if self.spoof is not None and sentence_type in SPOOF_TYPES:
            self._guard('spoof', self.spoof.evaluate, now)
        block = (self.spoof_action == 'block' and self.spoof is not None
                 and self.spoof.state == 'HIGH')
        code = self._forward_code(sentence_type, p.sentence_last_valid_talker, block)
        to_radio = bool(code & _TO_RADIO)
        to_wifi = bool(code & _TO_WIFI) and bool(self.broadcaster and self.broadcaster.active)
        if ((to_radio or to_wifi) and sentence_type in POSITION_TYPES and rx_ms is not None
                and ticks_diff(now, rx_ms) > MAX_AGE_MS):
            self.stale_dropped += 1
            self.last_fault = now          # a stall delayed this position: the radio must not see it late
            return
        text = p.last_valid_sentence
        if to_radio:
            try:
                self.radio.write(text)
                if sentence_type in POSITION_TYPES:
                    self.last_forward = now
            except Exception as e:         # e.g. an OSError on the UART: count it and carry on
                self.radio_errors += 1
                self.last_fault = now
                self._note_error('radio', e)
            if self.on_forward:
                self._guard('debug_print', self.on_forward, text)
        if to_wifi:
            self._wifi_lines.append(text)

    def _forward_code(self, sentence_type, talker, block):
        """_TO_RADIO | _TO_WIFI bits for a sentence (Wi-Fi still needs the access point to be up). The result
        of forward_decision() is remembered per (type, talker) so the per-sentence cost is two dict lookups
        and no allocation; it is recomputed when the lists are replaced (the menu assigns new tuples)."""
        if block:                                   # spoofing alert in block mode: rare, always decided afresh
            radio, wifi = forward_decision(sentence_type, talker, self.forward_types, self.forward_talkers,
                                           self.wifi_talkers, True, self.block_types, True)
            return (_TO_RADIO if radio else 0) | (_TO_WIFI if wifi else 0)
        cfg = self._fwd_cfg
        if (cfg[0] is not self.forward_types or cfg[1] is not self.forward_talkers
                or cfg[2] is not self.wifi_talkers or cfg[3] is not self.block_types
                or len(self._fwd_cache) > FORWARD_CACHE_MAX):
            self._fwd_cfg = [self.forward_types, self.forward_talkers, self.wifi_talkers, self.block_types]
            self._fwd_cache = {}
        by_talker = self._fwd_cache.get(sentence_type)
        if by_talker is None:
            by_talker = self._fwd_cache[sentence_type] = {}
        code = by_talker.get(talker)
        if code is None:
            radio, wifi = forward_decision(sentence_type, talker, self.forward_types, self.forward_talkers,
                                           self.wifi_talkers, False, self.block_types, True)
            code = by_talker[talker] = (_TO_RADIO if radio else 0) | (_TO_WIFI if wifi else 0)
        return code

    def _flush_wifi(self, now):
        b = self.broadcaster
        if b is not None and b.active:
            if self._wifi_lines:
                self._guard('wifi_send', b.send, ''.join(self._wifi_lines))
            if ticks_diff(now, self._last_wifi_poll) > WIFI_POLL_PERIOD_MS:
                self._last_wifi_poll = now
                self._guard('wifi_poll', b.poll)
        del self._wifi_lines[:]

    def log_alert(self, label, detail=''):
        """Add an entry to the alert history, stamped with the clock (local time if one is set)."""
        text = units.shift_time(self.parser.get_time_string())[:5]
        self.alert_log.add(text, label, detail)

    def _log_alerts(self):
        """Note every new alert, and every alert that gets worse, of the detectors in the alert history."""
        for tag, detector in (('SPF', self.spoof), ('JAM', self.detector)):
            rank = 0
            if detector is not None:
                rank = 2 if detector.state == 'HIGH' else 1 if detector.state == 'MEDIUM' else 0
            if rank > self._logged.get(tag, 0):
                self.log_alert(tag + ('!' if rank == 2 else '?'), getattr(detector, 'reason', ''))
            self._logged[tag] = rank

    def _periodic(self, now):
        self._log_alerts()
        if ticks_diff(now, self._last_stats) > STATS_PERIOD_MS:
            self._last_stats = now
            snap = self._guard('stats', self.parser.snapshot_and_reset)
            if snap is not None:
                snap['rcvpm'] = snap['rcv'] * 60000 / STATS_PERIOD_MS
                self.stats = snap
        if self.spoof is not None and ticks_diff(now, self._last_spoof) > SPOOF_IDLE_PERIOD_MS:
            self._last_spoof = now
            self._guard('spoof', self.spoof.evaluate, now)   # lets old evidence expire
        if self.detector is not None and ticks_diff(now, self._last_jam) > JAM_EVAL_PERIOD_MS:
            self._last_jam = now
            self._guard('jamming', self.detector.evaluate, now, not self.no_fix(now))
        if self.log:
            for cmd, flag in self.parser.pmtk_acks.items():
                if self._logged_acks.get(cmd) != flag:
                    self._logged_acks[cmd] = flag
                    self._guard('log', self.log, 'PMTK{} ack: {}'.format(cmd, flag))

    def _check_reader(self, now):
        reader = self.reader
        if reader is None:
            return
        self.watchdog.fatal = reader.fatal
        if reader.fatal and not self.watchdog.armed:
            # no sentence was ever seen, so the hardware watchdog cannot help: reset ourselves
            if self._fatal_since is None:
                self._fatal_since = now
            elif ticks_diff(now, self._fatal_since) > FATAL_GRACE_MS:
                self._fatal('GPS thread failed before the first sentence')
        else:
            self._fatal_since = None

    # --- failure containment ---------------------------------------------------------------
    def _note_error(self, name, exc):
        count = self.errors[name] = self.errors.get(name, 0) + 1
        self.last_error = (name, repr(exc))
        if self.report and (count == 1 or count % 100 == 0):   # a persistent fault must not flood the console
            try:
                self.report(name, exc)
            except Exception:
                pass

    def _guard(self, name, fn, *args):
        """Run an optional part; on failure count it, and after repeated failures leave it off for a while."""
        since = self._disabled_at.get(name)
        if since is not None:
            if ticks_diff(self.clock(), since) < DISABLE_MS:
                return None
            del self._disabled_at[name]
        try:
            result = fn(*args)
            self._strikes[name] = 0
            return result
        except Exception as e:
            self._note_error(name, e)
            self._strikes[name] = self._strikes.get(name, 0) + 1
            if self._strikes[name] >= STRIKES_TO_DISABLE:
                self._strikes[name] = 0
                self._disabled_at[name] = self.clock()
            return None

    def _critical_failure(self, name, exc):
        self._note_error(name, exc)
        self._critical += 1
        if self._critical >= CRITICAL_FAILURES_MAX:
            self._fatal('sentence path keeps failing: ' + repr(exc))

    def _fatal(self, reason):
        if self.log:
            try:
                self.log('FATAL: ' + reason)
            except Exception:
                pass
        if self.on_fatal:
            self.on_fatal(reason)
