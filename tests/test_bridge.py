import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import NMEA
import bridge as B
from bridge import Bridge, GpsReader, RxQueue, SentenceFramer, WatchdogPolicy, forward_decision
from make_sample import reference_forwarded
from test_jamming import with_checksum

DATA = os.path.join(os.path.dirname(__file__), 'data')


class Clock:
    def __init__(self, now=0):
        self.now = now

    def __call__(self):
        return self.now


class FakeRadio:
    def __init__(self):
        self.written = []
        self.error = None

    def write(self, text):
        if self.error:
            raise self.error
        self.written.append(text)


class FakeGps:
    """Scripted GPS UART: feed() adds bytes, uart_any/uart_receive_string hand them out."""

    def __init__(self):
        self.pending = b''
        self.fail = None

    def feed(self, data):
        self.pending += data

    def uart_any(self):
        if self.fail:
            raise self.fail
        return len(self.pending)

    def uart_receive_string(self, n):
        data, self.pending = self.pending[:n], self.pending[n:]
        return data


class FakeBroadcaster:
    def __init__(self):
        self.active = True
        self.sent = []
        self.polls = 0
        self.fail_send = False

    def send(self, text):
        if self.fail_send:
            raise OSError('boom')
        self.sent.append(text)

    def poll(self):
        self.polls += 1


class FakeDetector:
    def __init__(self, state='OK'):
        self.state = state
        self.calls = []
        self.fail = False

    def evaluate(self, now, *args):
        self.calls.append((now,) + args)
        if self.fail:
            raise RuntimeError('detector bug')
        return self.state, ''

    def signature(self):
        return (self.state,)


def make_bridge(**kw):
    clock = Clock(1000)
    parser = NMEA.Parser()
    queue = RxQueue(64)
    radio = FakeRadio()
    fatal = []
    log = []
    b = Bridge(parser, queue, radio, clock, on_fatal=fatal.append, log=log.append, **kw)
    b.fatal_calls = fatal
    b.log_lines = log
    b.radio_ = radio
    return b, clock


def push(b, sentence, rx_ms=None, now=None):
    b.queue.push((b.clock() if rx_ms is None else rx_ms, sentence))


GGA = with_checksum('GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,')
RMC = with_checksum('GNRMC,123519.000,A,4807.0380,N,01131.0000,E,5.00,90.00,040726,,,A')
GSV = with_checksum('GPGSV,1,1,02,01,40,083,46,02,50,100,44')
BDGSV = with_checksum('BDGSV,1,1,02,06,40,120,36,09,35,200,33')
ZDA = with_checksum('GNZDA,201530.00,04,07,2002,00,00')


# --- framer -------------------------------------------------------------------------------------
def test_framer_splits_lines_and_keeps_partial_data_between_feeds():
    f = SentenceFramer()
    assert f.feed(b'$GPGGA,1*00\r\n$GPRMC,2') == ['$GPGGA,1*00\r\n']
    assert f.feed(b'*00\r\n') == ['$GPRMC,2*00\r\n']


def test_framer_ends_a_line_at_the_next_dollar_when_there_is_no_terminator():
    f = SentenceFramer()
    assert f.feed(b'$GPGGA,1*00$GPRMC,2*00\n') == ['$GPGGA,1*00', '$GPRMC,2*00\n']


def test_framer_drops_non_printable_bytes_and_truncates_long_lines():
    f = SentenceFramer(max_len=20)
    assert f.feed(b'$GP\x00\xffGGA\n') == ['$GPGGA\n']
    out = f.feed(b'$' + b'A' * 50 + b'\n')
    assert len(out) == 1 and len(out[0]) == 20             # truncated, the checksum then fails downstream
    assert f.feed(b'$OK*00\n') == ['$OK*00\n']             # and it resynchronises on the next line


def test_framer_garbage_before_a_dollar_is_its_own_line():
    f = SentenceFramer()
    assert f.feed(b'abc$GPGGA,1*00\n') == ['abc', '$GPGGA,1*00\n']


# --- queue --------------------------------------------------------------------------------------
class CountingLock:
    def __init__(self):
        self.acquired = self.released = 0

    def acquire(self):
        self.acquired += 1

    def release(self):
        self.released += 1


def test_queue_orders_drops_oldest_and_counts():
    lock = CountingLock()
    q = RxQueue(3, lock)
    for i in range(5):
        q.push(i)
    assert q.dropped == 2 and q.drain() == [2, 3, 4]
    assert q.drain() == []
    assert lock.acquired == lock.released == 7             # always released


# --- forwarding policy --------------------------------------------------------------------------
def test_forward_decision_matrix():
    ft, ftk, wtk, bt = ('RMC', 'GGA', 'GSV'), ('GP', 'GN'), ('GP', 'GN', 'BD'), ('RMC', 'GGA')
    d = lambda t, k, block=False, wifi=True: forward_decision(t, k, ft, ftk, wtk, block, bt, wifi)
    assert d('RMC', 'GN') == (True, True)
    assert d('GSV', 'BD') == (False, True)                 # BeiDou: Wi-Fi only
    assert d('ZDA', 'GP') == (False, False)                # type not enabled
    assert d('RMC', 'II') == (False, False)                # unknown talker
    assert d('RMC', 'GN', wifi=False) == (True, False)
    assert d('RMC', 'GN', block=True) == (False, False)    # blocked position
    assert d('GSV', 'GP', block=True) == (True, True)      # block mode only holds back the position types


# --- watchdog policy ----------------------------------------------------------------------------
def test_watchdog_policy_arms_on_valid_sentences_and_stops_feeding_on_silence_or_fatal():
    w = WatchdogPolicy(silence_ms=30000)
    assert not w.should_arm() and not w.should_feed(0)
    w.on_valid(1000)
    assert w.should_arm()
    w.armed = True
    assert not w.should_arm()
    assert w.should_feed(20000) and not w.should_feed(31000)
    w.on_valid(31000)
    assert w.should_feed(40000)
    w.fatal = True
    assert not w.should_feed(40000)


# --- GPS reader ---------------------------------------------------------------------------------
def make_reader(init_result=4800):
    clock = Clock(0)
    q = RxQueue(16)
    gps = FakeGps()
    inits = []
    r = GpsReader(gps, q, clock, lambda: inits.append(clock.now) or init_result)
    return r, gps, q, clock, inits


def test_reader_stamps_each_sentence_when_its_line_completes():
    r, gps, q, clock, _ = make_reader()
    clock.now = 100
    gps.feed(b'$GPGGA,1*00\r\n$GPRMC,2')
    assert r.step()
    clock.now = 150
    gps.feed(b'*00\r\n')
    assert r.step()
    assert q.drain() == [(100, '$GPGGA,1*00\r\n'), (150, '$GPRMC,2*00\r\n')]
    assert not r.step()                                    # nothing waiting


def test_bridge_marks_the_reader_good_on_valid_sentences_only():
    b, clock = make_bridge()
    r, gps, q, rclock, _ = make_reader()
    r.start()
    b.reader = r
    clock.now = 500
    push(b, 'garbage line\r\n')
    push(b, '$GPGGA,1*FF\r\n')                             # unframed text and a bad checksum
    b.step(clock.now)
    assert r.last_good == 0
    clock.now = 900
    push(b, GGA)
    b.step(clock.now)
    assert r.last_good == 900


def test_reader_reprobes_when_the_module_goes_quiet_rate_limited():
    r, gps, q, clock, inits = make_reader()
    r.start()
    assert inits == [0] and r.found == 4800
    clock.now = B.REPROBE_AFTER_MS - 1
    r.maintain()
    assert inits == [0]                                    # not quiet long enough
    clock.now = B.REPROBE_AFTER_MS + 1
    r.maintain()
    assert len(inits) == 2                                 # quiet for 30 s: look for the module again
    clock.now += 10000
    r.maintain()
    assert len(inits) == 2                                 # and not again straight away
    clock.now += B.REPROBE_EVERY_MS
    r.maintain()
    assert len(inits) == 3                                 # still quiet a minute later: try again


class Stop(BaseException):
    pass


def test_reader_thread_survives_errors_reports_them_and_recovers():
    r, gps, q, clock, inits = make_reader()
    reports, sleeps = [], []
    calls = {'n': 0}
    original_init = r.init_fn

    def flaky_init():
        calls['n'] += 1
        if calls['n'] == 1:
            raise OSError('uart exploded')
        return original_init()
    r.init_fn = flaky_init

    def sleep_ms(ms):
        sleeps.append(ms)
        if len(sleeps) == 1:
            assert r.fatal and r.errors == 1               # visible to the main loop while it is down
        if len(sleeps) >= 2:
            raise Stop()
    try:
        r.run_forever(sleep_ms, lambda name, e: reports.append((name, repr(e))))
    except Stop:
        pass
    assert reports and reports[0][0] == 'gps thread'
    assert sleeps[0] == 1000                               # backed off before retrying
    assert not r.fatal and calls['n'] == 2                 # recovered: init succeeded the second time


# --- bridge: the radio path ---------------------------------------------------------------------
def load_sample():
    with open(os.path.join(DATA, 'sample.nmea')) as f:
        return [(int(line.split(' ', 1)[0]), line.split(' ', 1)[1].rstrip('\n')) for line in f]


def run_sample():
    clock = Clock()
    parser = NMEA.Parser()
    q = RxQueue(64)
    radio = FakeRadio()
    b = Bridge(parser, q, radio, clock)
    gps = FakeGps()
    reader = GpsReader(gps, q, clock, lambda: 4800)
    for ms, line in load_sample():
        clock.now = ms
        gps.feed((line + '\r\n').encode())                 # bytes -> framer -> queue -> bridge -> radio
        reader.step()
        b.step(ms)
    return radio.written, b


def test_golden_radio_output_for_the_sample_log():
    written, b = run_sample()
    with open(os.path.join(DATA, 'sample_forwarded.nmea'), newline='') as f:
        golden = f.read()
    assert ''.join(written) == golden                      # byte for byte what the radio must receive
    assert written == reference_forwarded([t for _, t in load_sample()])
    assert not any(s.startswith('$GN') for s in written)   # talker rewritten for the radio
    assert all(s.endswith('\r\n') and s.count('\n') == 1 for s in written)
    assert b.stale_dropped == 0 and not b.errors


def test_sample_exercises_the_interesting_cases():
    written, b = run_sample()
    text = ''.join(written)
    assert '$GPGGA,,,,,,,,,,,,,,*' in text                 # valid envelope, unreadable fields: still forwarded
    assert '$GPRMC,123519*' in text                        # truncated but well-formed: forwarded
    assert 'BDGSV' not in text and 'BDGSA' not in text     # BeiDou is not forwarded to the radio
    assert 'PMTK' not in text and 'TXT' not in text
    p = NMEA.Parser()                                      # what the parser makes of the oddities in the sample
    for _, line in load_sample():
        p.parse_sentence(line)
    assert p.sentences_invalid == 2                        # the bad-checksum line and the line without a '$'
    assert p.parse_errors == 2                             # the empty GGA and the truncated RMC: forwarded anyway


def test_failing_optional_parts_never_stop_forwarding():
    b, clock = make_bridge()
    b.spoof = FakeDetector()
    b.spoof.fail = True
    b.detector = FakeDetector()
    b.detector.fail = True
    b.broadcaster = FakeBroadcaster()
    b.broadcaster.fail_send = True
    b.on_forward = lambda text: 1 / 0
    b.on_raw = lambda rx, s: 1 / 0
    b.radio.error = None
    for i in range(10):
        clock.now += 1000
        push(b, RMC)
        push(b, GGA)
        b.step(clock.now)
    assert len(b.radio_.written) == 20                     # every sentence reached the radio regardless
    for name in ('spoof', 'jamming', 'wifi_send', 'debug_print', 'log_raw'):
        assert b.errors[name] >= 1, name


def test_a_failing_part_is_switched_off_for_a_while_then_retried():
    b, clock = make_bridge()
    b.spoof = FakeDetector()
    b.spoof.fail = True
    for i in range(6):
        push(b, RMC)
        b.step(clock.now)
    assert len(b.spoof.calls) == B.STRIKES_TO_DISABLE      # disabled after repeated failures
    clock.now += B.DISABLE_MS + 1
    push(b, RMC)
    b.step(clock.now)
    assert len(b.spoof.calls) > B.STRIKES_TO_DISABLE       # retried once the pause is over


def test_radio_write_errors_are_counted_and_do_not_stop_wifi_or_later_sentences():
    b, clock = make_bridge()
    b.broadcaster = FakeBroadcaster()
    b.radio.error = OSError(5, 'uart')
    push(b, RMC)
    b.step(clock.now)
    assert b.radio_errors == 1 and b.broadcaster.sent == [b.parser.last_valid_sentence]
    b.radio.error = None
    push(b, GGA)
    b.step(clock.now)
    assert len(b.radio_.written) == 1


def test_parser_bug_drops_the_sentence_but_repeated_bugs_end_in_a_reset():
    b, clock = make_bridge()

    def broken(sentence, rx_ms=None):
        raise RuntimeError('parser bug')
    b.parser.parse_sentence = broken
    for _ in range(B.CRITICAL_FAILURES_MAX - 1):
        push(b, RMC)
    b.step(clock.now)
    assert b.radio_.written == [] and not b.fatal_calls    # nothing forwarded unchecked, no reset yet
    push(b, RMC)
    b.step(clock.now)
    assert len(b.fatal_calls) == 1                          # the sentence path keeps failing: reset


def test_a_good_sentence_resets_the_critical_failure_count():
    b, clock = make_bridge()
    real = b.parser.parse_sentence
    state = {'bad': True}

    def sometimes(sentence, rx_ms=None):
        if state['bad']:
            raise RuntimeError('bug')
        return real(sentence, rx_ms)
    b.parser.parse_sentence = sometimes
    for _ in range(B.CRITICAL_FAILURES_MAX - 1):
        push(b, RMC)
    b.step(clock.now)
    state['bad'] = False
    push(b, RMC)
    b.step(clock.now)
    state['bad'] = True
    for _ in range(B.CRITICAL_FAILURES_MAX - 1):
        push(b, RMC)
    b.step(clock.now)
    assert not b.fatal_calls


def test_stale_positions_are_not_forwarded_but_other_sentences_are():
    b, clock = make_bridge()
    clock.now = 100000
    push(b, RMC, rx_ms=clock.now - B.MAX_AGE_MS - 1)       # waited in the queue through a long stall
    push(b, GGA, rx_ms=clock.now - 10)
    push(b, GSV, rx_ms=clock.now - 20000)
    b.step(clock.now)
    texts = b.radio_.written
    assert len(texts) == 2 and texts[0].startswith('$GPGGA') and texts[1].startswith('$GPGSV')
    assert b.stale_dropped == 1


def test_block_mode_holds_back_positions_only_while_alerting():
    b, clock = make_bridge()
    b.spoof = FakeDetector('OK')
    b.spoof_action = 'block'
    b.broadcaster = FakeBroadcaster()
    for s in (RMC, GGA, ZDA):
        push(b, s)
    b.step(clock.now)
    assert len(b.radio_.written) == 3 and len(b.broadcaster.sent[0].split('\r\n')) == 4
    b.spoof.state = 'ALERT'
    b.radio_.written.clear()
    b.broadcaster.sent.clear()
    for s in (RMC, GGA, ZDA, GSV):
        push(b, s)
    b.step(clock.now)
    assert [t[3:6] for t in b.radio_.written] == ['ZDA', 'GSV']
    assert b.broadcaster.sent == [''.join(b.radio_.written)]        # Wi-Fi gets the same, nothing more
    b.spoof_action = 'display'
    push(b, RMC)
    b.step(clock.now)
    assert b.radio_.written[-1].startswith('$GPRMC')


def test_wifi_gets_one_batched_send_per_step_and_beidou():
    b, clock = make_bridge()
    b.broadcaster = FakeBroadcaster()
    for s in (RMC, GGA, BDGSV):
        push(b, s)
    b.step(clock.now)
    assert len(b.broadcaster.sent) == 1
    assert b.broadcaster.sent[0].count('\r\n') == 3 and 'BDGSV' in b.broadcaster.sent[0]
    assert len(b.radio_.written) == 2                      # the radio does not get BeiDou
    b.step(clock.now)
    assert len(b.broadcaster.sent) == 1                    # nothing new: no empty send
    b.broadcaster.active = False
    push(b, RMC)
    b.step(clock.now)
    assert len(b.broadcaster.sent) == 1


def test_wifi_is_polled_for_clients_periodically():
    b, clock = make_bridge()
    b.broadcaster = FakeBroadcaster()
    b.step(clock.now)
    assert b.broadcaster.polls == 0
    clock.now += B.WIFI_POLL_PERIOD_MS + 1
    b.step(clock.now)
    assert b.broadcaster.polls == 1


def test_watchdog_arms_on_valid_sentences_not_on_garbage():
    b, clock = make_bridge()
    for s in ('garbage', '$GPGGA,1*00', 'noise$$$'):
        push(b, s)
    b.step(clock.now)
    assert not b.watchdog.should_arm()
    push(b, RMC)
    b.step(clock.now)
    assert b.watchdog.should_arm()


def test_dead_gps_thread_before_the_first_sentence_resets_after_the_grace_period():
    b, clock = make_bridge()
    reader = type('R', (), {'fatal': True})()
    b.reader = reader
    b.step(clock.now)
    assert not b.fatal_calls
    clock.now += B.FATAL_GRACE_MS + 1
    b.step(clock.now)
    assert len(b.fatal_calls) == 1
    reader.fatal = False
    b.fatal_calls.clear()
    clock.now += 10000
    b.step(clock.now)
    assert not b.fatal_calls and not b.watchdog.fatal


def test_gps_thread_failure_after_arming_stops_the_watchdog_feed_instead_of_resetting_itself():
    b, clock = make_bridge()
    push(b, RMC)
    b.step(clock.now)
    b.watchdog.armed = True
    reader = type('R', (), {'fatal': True})()
    b.reader = reader
    clock.now += 10 * B.FATAL_GRACE_MS
    b.step(clock.now)
    assert not b.fatal_calls                               # the hardware watchdog will do the reset
    assert not b.watchdog.should_feed(clock.now)


def test_periodic_tasks_stats_detectors_and_ack_logging():
    b, clock = make_bridge()
    b.detector = FakeDetector()
    b.spoof = FakeDetector()
    push(b, RMC)
    push(b, GGA)                                            # GGA carries the fix quality
    push(b, with_checksum('PMTK001,286,3'))
    b.step(clock.now)
    assert b.log_lines == ['PMTK286 ack: 3']
    t0 = clock.now
    clock.now = t0 + B.JAM_EVAL_PERIOD_MS + 1
    b.step(clock.now)
    assert b.detector.calls[-1][0] == clock.now and b.detector.calls[-1][1] is True   # a fix is available
    assert b.log_lines == ['PMTK286 ack: 3']               # each ack is logged once
    clock.now = t0 + B.STATS_PERIOD_MS + 1
    b.step(clock.now)
    assert b.stats['rcv'] == 3 and b.stats['rcvpm'] == 18  # 3 sentences in 10 s -> 18 per minute
    assert b.parser.sentences_received == 0                # counters were reset for the next window


def test_no_fix_logic_and_alert():
    b, clock = make_bridge()
    assert b.no_fix(clock.now)                             # nothing received yet
    push(b, GGA)
    b.step(clock.now)
    assert not b.no_fix(clock.now)
    assert b.no_fix(clock.now + B.FIX_STALE_TIMEOUT_MS + 1)
    assert not b.alert()
    b.detector = FakeDetector('LOW')
    assert b.alert()
    b.detector.state, b.spoof = 'OK', FakeDetector('SUSPECT')
    assert b.alert()
    b.spoof.state = 'OK'
    assert not b.alert()


def test_stale_check_tolerates_a_missing_arrival_time():
    b, clock = make_bridge()
    b.queue.push((None, RMC))                              # as produced when no timing is known
    b.step(clock.now)
    assert len(b.radio_.written) == 1


def test_tick_wrap_does_not_confuse_age_or_periodic_checks():
    saved = B.ticks_diff
    B.ticks_diff = lambda a, b: ((a - b + (1 << 29)) & ((1 << 30) - 1)) - (1 << 29)
    try:
        b, clock = make_bridge()
        clock.now = (1 << 30) - 500
        b._last_stats = b._last_jam = b._last_spoof = b._last_wifi_poll = clock.now
        b.detector = FakeDetector()
        push(b, RMC, rx_ms=clock.now)
        b.step(clock.now)
        clock.now = (clock.now + B.JAM_EVAL_PERIOD_MS + 10) % (1 << 30)    # wraps to a small number
        b.step(clock.now)
        assert len(b.radio_.written) == 1 and len(b.detector.calls) == 1
        assert b.stale_dropped == 0
    finally:
        B.ticks_diff = saved


# --- raw logger ---------------------------------------------------------------------------------
def test_raw_logger_prints_arrival_time_and_the_sentence_as_received():
    lines, clock = [], Clock(0)
    log = B.RawLogger(lines.append, clock)
    log(123456, '$GNRMC,1*00\r\n')
    log(123500, '$BDGSV,1,1,00*00\n')
    assert lines == ['123456 $GNRMC,1*00', '123500 $BDGSV,1,1,00*00']   # replay.py reads this format
    assert not log.disabled


def test_raw_logger_switches_itself_off_when_the_console_is_slow():
    clock = Clock(0)
    seen, disabled = [], []

    def slow_print(text):
        seen.append(text)
        clock.now += 500                                 # a console that is attached but not draining
    log = B.RawLogger(slow_print, clock, max_ms=200, on_disable=lambda: disabled.append(True))
    log(1, '$A*00')
    assert log.disabled and disabled == [True]
    log(2, '$B*00')
    assert len(seen) == 1                                # nothing more is written once it gave up


def test_bridge_calls_the_raw_logger_for_every_sentence_before_parsing():
    b, clock = make_bridge()
    seen = []
    b.on_raw = lambda rx, s: seen.append((rx, s))
    push(b, 'garbage')
    push(b, BDGSV)
    b.step(clock.now)
    assert [s for _, s in seen] == ['garbage', BDGSV]    # invalid lines and BeiDou too, unmodified


def test_gn_to_gp_checksum_is_updated_incrementally():
    p = NMEA.Parser()
    for body in ('GNGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,', 'GNZDA,201530.00,04,07,2002,00,00',
                 'GNGSA,A,3,01,02,03,,,,,,,,,,1.0,1.0,1.0'):
        s = with_checksum(body)
        assert p.parse_sentence(s)
        assert p.last_valid_sentence == with_checksum('GP' + body[2:]) + '\r\n'


def test_forward_cache_follows_replaced_type_and_talker_lists():
    b, clock = make_bridge()
    push(b, RMC)
    push(b, GSV)
    b.step(clock())
    assert [s[3:6] for s in b.radio_.written] == ['RMC', 'GSV']
    b.forward_types = ('RMC',)                 # the menu assigns a new tuple
    push(b, RMC)
    push(b, GSV)
    b.step(clock())
    assert [s[3:6] for s in b.radio_.written] == ['RMC', 'GSV', 'RMC']
    b.forward_types = ('RMC', 'GSV')
    b.forward_talkers = ('GN',)                # only combined-talker sentences from now on
    push(b, RMC)                               # GN: still forwarded
    push(b, GSV)                               # GP: no longer
    b.step(clock())
    assert [s[3:6] for s in b.radio_.written] == ['RMC', 'GSV', 'RMC', 'RMC']


def test_forward_cache_matches_forward_decision_for_all_combinations():
    b, clock = make_bridge()
    b.forward_types = ('RMC', 'GSV')
    for block in (False, True):
        for t in ('RMC', 'GGA', 'GSV', 'XXX'):
            for tk in ('GP', 'GN', 'BD', 'GL'):
                want = B.forward_decision(t, tk, b.forward_types, b.forward_talkers, b.wifi_talkers,
                                          block, b.block_types, True)
                code = b._forward_code(t, tk, block)
                assert (bool(code & 1), bool(code & 2)) == want
