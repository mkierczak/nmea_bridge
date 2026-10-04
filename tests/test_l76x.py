import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import NMEA
from test_jamming import with_checksum


class Clock:
    now = 0


class Module:
    """Simulated L76B: talks only at its own baud, switches on $PMTK251,<n>."""
    baud = 9600
    present = True
    commands = []
    rx = b''


def _reset(baud=9600, present=True):
    Clock.now = 0
    Module.baud, Module.present, Module.commands, Module.rx = baud, present, [], b''
    FakeUART.instances = []


GGA = with_checksum('GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,') + '\r\n'


class FakeUART:
    instances = []

    def __init__(self, uart_id, baudrate=9600, tx=None, rx=None, rxbuf=0):
        self.baudrate = baudrate
        self._buf = b''
        self._last = Clock.now
        FakeUART.instances.append(self)

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
                Module.baud = int(text[9:].split('*')[0])


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
