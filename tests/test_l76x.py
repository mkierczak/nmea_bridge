import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import NMEA
from test_jamming import with_checksum


class Clock:
    now = 0


ENGINE_COMMANDS = (869, 220, 225, 353)   # after these the real module ignores commands for about a second


class Module:
    """Simulated L76B: talks only at its own baud, switches on $PMTK251,<n>, acknowledges PMTK commands
    with $PMTK001 and drops commands that arrive while it is busy restarting its engine."""
    baud = 9600
    present = True
    commands = []
    rx = b''
    busy_until = -1
    flags = {}            # command number -> flag to answer with (default 3)
    silent = ()           # command numbers that never get a reply
    accepted = []         # command numbers the module actually processed
    heavy = False         # default output too large: refuses $PMTK251 (flag 2) until $PMTK314 reduced it


def _reset(baud=9600, present=True):
    Clock.now = 0
    Module.baud, Module.present, Module.commands, Module.rx = baud, present, [], b''
    Module.busy_until, Module.flags, Module.silent, Module.accepted = -1, {}, (), []
    Module.heavy = False
    FakeUART.instances = []


GGA = with_checksum('GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,') + '\r\n'


class FakeUART:
    instances = []

    def __init__(self, uart_id, baudrate=9600, tx=None, rx=None, rxbuf=0):
        self.baudrate = baudrate
        self._buf = b''
        self._last = Clock.now
        FakeUART.instances.append(self)

    def init(self, baudrate=None, **kwargs):
        if baudrate is not None:
            self.baudrate = baudrate
        self._buf = b''

    def any(self):
        if Clock.now - self._last >= 200:  # the module emits a sentence every 200 virtual ms
            self._last = Clock.now
            if Module.present:
                self._buf += GGA.encode() if self.baudrate == Module.baud else b'\xe0\x80\x1f\xfe\x00'
        return len(self._buf)

    def read(self, n=None):
        n = len(self._buf) if n is None else n
        data, self._buf = self._buf[:n], self._buf[n:]
        return data

    def write(self, data):
        if not Module.present or self.baudrate != Module.baud:
            return
        Module.rx += data
        while b'\n' in Module.rx:
            line, Module.rx = Module.rx.split(b'\n', 1)
            text = line.strip().decode()
            Module.commands.append(text)
            if text.startswith('$PMTK251,'):
                if Module.heavy:
                    self._buf += (with_checksum('PMTK001,251,2') + '\r\n').encode()
                else:
                    Module.baud = int(text[9:].split('*')[0])
            elif text.startswith('$PMTK'):
                self._pmtk(text)

    def _pmtk(self, text):
        cmd = int(text[5:].split(',')[0].split('*')[0])
        if Clock.now < Module.busy_until:
            return                                    # busy: the command is silently lost
        Module.accepted.append(cmd)
        if cmd == 314:
            Module.heavy = False
        if cmd in ENGINE_COMMANDS:
            Module.busy_until = Clock.now + 1000
        if cmd in Module.silent:
            return
        self._buf += (with_checksum('PMTK001,%d,%d' % (cmd, Module.flags.get(cmd, 3))) + '\r\n').encode()


def _fake_modules():
    machine = types.ModuleType('machine')
    machine.UART = FakeUART
    machine.Pin = lambda n, *a, **k: n
    utime = types.ModuleType('utime')
    utime.ticks_ms = lambda: Clock.now
    utime.ticks_diff = lambda a, b: a - b

    def sleep(s):
        Clock.now += int(s * 1000)

    def sleep_ms(ms):
        Clock.now += ms

    utime.sleep, utime.sleep_ms = sleep, sleep_ms
    return {'machine': machine, 'utime': utime}


_saved = {k: sys.modules.get(k) for k in ('machine', 'utime', 'l76x')}
sys.modules.update(_fake_modules())
sys.modules.pop('l76x', None)
import l76x  # noqa: E402  (imported against the fakes)
for _k, _v in _saved.items():  # don't leak the stubs into other test modules
    if _k == 'l76x':
        continue
    if _v is None:
        sys.modules.pop(_k, None)
    else:
        sys.modules[_k] = _v


def new_gps(target=4800):
    return l76x.L76X(uartx=0, _baudrate=target)


def test_already_at_target_sends_nothing():
    _reset(baud=4800)
    gps = new_gps()
    assert gps.configure_baudrate(4800) == 4800
    assert Module.commands == []
    assert gps.baudrate == 4800


def test_module_at_9600_is_found_and_switched():
    _reset(baud=9600)
    gps = new_gps()
    assert gps.configure_baudrate(4800) == 9600
    assert len(Module.commands) == 1
    cmd = Module.commands[0]
    assert cmd.startswith('$PMTK251,4800*') and NMEA.valid_checksum(cmd)
    assert Module.baud == 4800 and gps.baudrate == 4800


def test_a_module_that_refuses_the_switch_is_configured_first_then_switched():
    _reset(baud=9600)
    Module.heavy = True                                  # a power-cycled module: full default output
    gps = new_gps()
    calls = []

    def prepare():
        calls.append(gps.baudrate)                       # runs at the module's rate, before the switch
        gps.send_command('$PMTK314,0,1,0,1,5,5,0,0,0,0,0,0,0,0,0,0,0,1,0')
    assert gps.configure_baudrate(4800, prepare=prepare) == 9600
    assert calls == [9600] and Module.baud == 4800 and gps.baudrate == 4800


def test_a_switch_that_stays_refused_leaves_the_uart_at_the_modules_rate():
    _reset(baud=9600)
    Module.heavy = True
    gps = new_gps()
    assert gps.configure_baudrate(4800) == 9600          # not None: it was heard
    assert Module.baud == 9600 and gps.baudrate == 9600  # and the data keeps flowing, at the old rate


def test_module_at_115200_is_found():
    _reset(baud=115200)
    gps = new_gps()
    assert gps.configure_baudrate(9600) == 115200
    assert Module.baud == 9600 and gps.baudrate == 9600


def test_silent_module_returns_none_and_leaves_uart_at_target():
    _reset(present=False)
    gps = new_gps()
    assert gps.configure_baudrate(4800) is None
    assert gps.baudrate == 4800
    assert Module.commands == []
    assert Clock.now < 20000  # bounded probing time (virtual ms)


def test_invalid_rate_rejected():
    _reset()
    gps = new_gps()
    for bad in (1200, 0, 12345):
        try:
            gps.configure_baudrate(bad)
        except ValueError:
            continue
        raise AssertionError('accepted {}'.format(bad))
    assert Module.commands == []


def test_commands():
    assert l76x.baud_command(4800) == '$PMTK251,4800'
    assert l76x.fix_interval_command(1000) == '$PMTK220,1000'
    try:
        l76x.fix_interval_command(50)
    except ValueError:
        pass
    else:
        raise AssertionError


def test_load_estimates():
    avg, worst = l76x.nmea_load(4800, 1000, beidou=False)
    assert avg < 0.8 and worst < 1.0                    # GPS only fits at 4800 / 1 s
    avg, worst = l76x.nmea_load(4800, 1000, beidou=True)
    assert avg < 0.8 and worst > 1.0                    # GPS+BeiDou: GSA/GSV cycle exceeds one interval
    avg, worst = l76x.nmea_load(9600, 800, beidou=True)
    assert worst < 1.0
    assert 1000 < l76x.nmea_burst_ms(4800, True) < 2000
    assert l76x.nmea_burst_ms(9600, True) < l76x.nmea_burst_ms(4800, True)


def test_valid_checksum_helper():
    assert NMEA.valid_checksum(with_checksum('GPGGA,1,2'))
    assert not NMEA.valid_checksum('$GPGGA,1,2*00')
    assert not NMEA.valid_checksum('$GPGGA,1,2')
    assert not NMEA.valid_checksum('$GPGGA,1,2*ZZ')


def test_probing_reuses_one_uart_instead_of_creating_one_per_rate():
    _reset(baud=115200)
    gps = new_gps()
    assert gps.configure_baudrate(9600) == 115200
    assert len(FakeUART.instances) == 1                    # was one new UART (and 1 KB buffer) per probe
    assert FakeUART.instances[0].baudrate == 9600


SEQUENCE = ['$PMTK352,0', '$PMTK313,1', '$PMTK301,2', '$PMTK869,1,1', '$PMTK220,1000', '$PMTK225,0',
            '$PMTK353,1,0,0,0,1', '$PMTK255,1', '$PMTK314,0,1,0,1,5,5,0,0,0,0,0,0,0,0,0,0,0,1,0',
            '$PMTK286,1', '$PMTK838,1']


def test_command_helpers():
    assert l76x.command_number('$PMTK220,1000') == 220 and l76x.command_number('$PMTK838,1') == 838
    assert l76x.command_number('$PMTK101') == 101
    assert l76x.command_number('$PQ1PPS,1') is None and l76x.command_number('$PMTKxyz') is None
    assert l76x.ack_flag(with_checksum('PMTK001,220,3,1000'), 220) == 3
    assert l76x.ack_flag(with_checksum('PMTK001,220,3,1000'), 225) is None      # an ack for another command
    assert l76x.ack_flag(with_checksum('PMTK001,353,1'), 353) == 1
    assert l76x.ack_flag('$PMTK001,220,3*00', 220) is None                       # bad checksum
    assert l76x.ack_flag(with_checksum('PMTKSPF,1'), 220) is None
    assert l76x.ack_flag(with_checksum('GPGGA,1,2'), 220) is None


def test_the_old_fire_and_forget_sequence_loses_commands_on_a_module_that_restarts_its_engine():
    _reset(baud=4800)
    gps = new_gps()
    for cmd in SEQUENCE:                                   # what the app used to do: 100 ms apart, no waiting
        gps.send_command(cmd)
    assert len(Module.accepted) < len(SEQUENCE)            # some commands never took effect (as on the board)
    assert 220 not in Module.accepted or 353 not in Module.accepted or 838 not in Module.accepted


def test_acknowledged_commands_all_take_effect_even_when_the_module_is_busy():
    _reset(baud=4800)
    gps = new_gps()
    failed = gps.send_commands(SEQUENCE)
    assert failed == []
    for cmd in (352, 313, 301, 869, 220, 225, 353, 255, 314, 286, 838):
        assert cmd in Module.accepted, cmd                 # every command was processed by the module
    assert Clock.now < 30000                               # and the whole sequence stays quick (virtual ms)


def test_a_command_is_resent_when_the_module_did_not_hear_it():
    _reset(baud=4800)
    Module.busy_until = 600                                # busy right now: the first attempt is lost
    gps = new_gps()
    assert gps.send_command_acked('$PMTK220,1000') == 3
    sent = [c for c in Module.commands if c.startswith('$PMTK220')]
    assert len(sent) >= 2 and Module.accepted == [220]     # resent, processed once


def test_an_unanswered_command_is_reported_after_bounded_retries():
    _reset(baud=4800)
    Module.silent = (838,)
    gps = new_gps()
    start = Clock.now
    assert gps.send_command_acked('$PMTK838,1', timeout_ms=500, retries=3) is None
    assert len([c for c in Module.commands if c.startswith('$PMTK838')]) == 3
    assert Clock.now - start < 3000
    assert gps.send_commands(['$PMTK838,1', '$PMTK286,1'], timeout_ms=500, retries=2) == [('$PMTK838,1', None)]


def test_unsupported_and_failed_flags():
    _reset(baud=4800)
    Module.flags = {405: 1, 286: 2}
    gps = new_gps()
    assert gps.send_command_acked('$PMTK405') == 1                         # unsupported: not retried
    assert len([c for c in Module.commands if c.startswith('$PMTK405')]) == 1
    assert gps.send_command_acked('$PMTK286,1', timeout_ms=400, retries=2) == 2   # valid but failed: retried
    assert len([c for c in Module.commands if c.startswith('$PMTK286')]) == 2
    failed = gps.send_commands(['$PMTK405', '$PMTK313,1'], timeout_ms=400, retries=2)
    assert failed == [('$PMTK405', 1)]


def test_acks_that_arrive_between_ordinary_sentences_are_found():
    _reset(baud=4800)
    gps = new_gps()
    assert gps.send_command_acked('$PMTK313,1') == 3       # the fake also emits a GGA every 200 ms meanwhile


def test_acknowledgements_are_handed_on_so_the_parser_still_sees_them():
    import NMEA
    _reset(baud=4800)
    gps = new_gps()
    lines = []
    assert gps.send_commands(['$PMTK220,1000', '$PMTK286,1'], on_ack=lines.append) == []
    assert len(lines) == 2 and all(line.startswith('$PMTK001,') for line in lines)
    parser = NMEA.Parser()
    for line in lines:
        assert parser.parse_sentence(line)
    assert parser.pmtk_acks == {220: 3, 286: 3}            # what the Debug page's AIC status reads
