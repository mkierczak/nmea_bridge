import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import wifi
from wifi import NmeaBroadcaster


class FakeConn:
    def __init__(self, mode='ok'):
        self.mode = mode        # ok | partial | eagain | reset
        self.sent = b''
        self.closed = False

    def setblocking(self, flag):
        pass

    def send(self, data):
        if self.mode == 'eagain':
            raise OSError(11, 'EAGAIN')
        if self.mode == 'reset':
            raise OSError(104, 'ECONNRESET')
        if self.mode == 'partial':
            self.sent += data[:3]
            return 3
        self.sent += data
        return len(data)

    def close(self):
        self.closed = True


class FakeSock:
    def __init__(self, kind):
        self.kind = kind
        self.pending = []       # connections waiting in accept()
        self.datagrams = []
        self.closed = False
        self.bound = None
        self.opts = []

    def setsockopt(self, *args):
        self.opts.append(args)

    def bind(self, addr):
        self.bound = addr

    def listen(self, n):
        pass

    def setblocking(self, flag):
        pass

    def accept(self):
        if not self.pending:
            raise OSError(11, 'EAGAIN')
        return self.pending.pop(0), ('192.168.4.2', 5555)

    def sendto(self, data, addr):
        self.datagrams.append((data, addr))

    def close(self):
        self.closed = True


class FakeSocketModule:
    AF_INET, SOCK_STREAM, SOCK_DGRAM, SOL_SOCKET, SO_REUSEADDR = 2, 1, 2, 1, 4

    def __init__(self):
        self.created = []

    def socket(self, family, kind):
        s = FakeSock('tcp' if kind == self.SOCK_STREAM else 'udp')
        self.created.append(s)
        return s


class FakeWLAN:
    def __init__(self):
        self.is_active = False
        self.cfg = {}

    def config(self, **kw):
        self.cfg.update(kw)

    def active(self, flag=None):
        if flag is None:
            return self.is_active
        self.is_active = flag

    def ifconfig(self):
        return ('192.168.4.1', '255.255.255.0', '192.168.4.1', '8.8.8.8')


class FakeNetwork:
    AP_IF = 1

    def __init__(self):
        self.wlan = FakeWLAN()

    def country(self, code=None):
        self.country_set = code

    def WLAN(self, mode):
        return self.wlan


def make(max_clients=4, password='secret123'):
    net, sock = FakeNetwork(), FakeSocketModule()
    b = NmeaBroadcaster(net, sock, 'nmea-bridge', password, port=10110, max_clients=max_clients)
    return b, net, sock


def started(**kw):
    b, net, sock = make(**kw)
    b.start()
    return b, net, sock, sock.created[0], sock.created[1]   # broadcaster, net, sock, tcp listener, udp


def test_broadcast_address_and_chunking():
    assert wifi.broadcast_address('192.168.4.1', '255.255.255.0') == '192.168.4.255'
    assert wifi.broadcast_address('10.1.2.3', '255.255.0.0') == '10.1.255.255'
    data = b''.join(b'$GPGGA,%03d\r\n' % i for i in range(300))
    pieces = wifi.chunk_lines(data, 1000)
    assert b''.join(pieces) == data
    assert all(len(p) <= 1000 and p.endswith(b'\n') for p in pieces)
    assert wifi.chunk_lines(b'x' * 2500, 1000) == [b'x' * 1000, b'x' * 1000, b'x' * 500]


def test_start_configures_wpa2_ap_and_sockets():
    b, net, sock, tcp, udp = started()
    assert net.wlan.is_active
    assert net.wlan.cfg == {'essid': 'nmea-bridge', 'password': 'secret123', 'security': wifi.SECURITY_WPA2_AES}
    assert tcp.bound == ('0.0.0.0', 10110)
    assert b.active and b.ip == '192.168.4.1'
    assert b.info() == ('ON sta0', 'nmea-bridge', '192.168.4.1', 0, 4)


def test_short_password_rejected_open_network_not_possible():
    b, net, sock = make(password='short')
    try:
        b.start()
    except ValueError:
        pass
    else:
        raise AssertionError('accepted a short password')
    assert not net.wlan.is_active and not b.active


def test_ap_that_never_comes_up_reports_error_and_cleans_up():
    b, net, sock = make()
    net.wlan.active = lambda flag=None: False if flag is None else None
    try:
        b.start(wait_ms=300)
    except OSError:
        pass
    else:
        raise AssertionError('should have failed')
    assert not b.active and b.info()[0] == 'ERR'


def test_clients_accepted_up_to_max_and_extras_closed():
    b, net, sock, tcp, udp = started(max_clients=2)
    conns = [FakeConn() for _ in range(3)]
    tcp.pending.extend(conns)
    b.poll()
    assert b.client_count == 2
    assert conns[2].closed and not conns[0].closed


def test_send_reaches_all_clients_and_udp():
    b, net, sock, tcp, udp = started()
    a, c = FakeConn(), FakeConn()
    tcp.pending.extend([a, c])
    b.poll()
    b.send('$GPRMC,1*00\r\n$GPGGA,2*00\r\n')
    assert a.sent == c.sent == b'$GPRMC,1*00\r\n$GPGGA,2*00\r\n'
    assert udp.datagrams == [(b'$GPRMC,1*00\r\n$GPGGA,2*00\r\n', ('192.168.4.255', 10110))]
    assert b.bytes_sent == 2 * len(a.sent)


def test_reset_client_dropped_others_unaffected():
    b, net, sock, tcp, udp = started()
    good, bad = FakeConn(), FakeConn('reset')
    tcp.pending.extend([bad, good])
    b.poll()
    b.send('$X*00\r\n')
    assert bad.closed and b.client_count == 1
    assert good.sent == b'$X*00\r\n'


def test_blocked_client_dropped_after_strikes_without_blocking():
    b, net, sock, tcp, udp = started()
    slow, fine = FakeConn('eagain'), FakeConn()
    tcp.pending.extend([slow, fine])
    b.poll()
    for _ in range(wifi.STRIKES_MAX - 1):
        b.send('$X*00\r\n')
    assert b.client_count == 2 and not slow.closed
    b.send('$X*00\r\n')
    assert slow.closed and b.client_count == 1
    assert fine.sent == b'$X*00\r\n' * wifi.STRIKES_MAX


def test_partial_sends_never_cut_a_sentence_in_the_middle():
    b, net, sock, tcp, udp = started()
    conn = FakeConn('partial')                       # accepts 3 bytes per send call
    tcp.pending.append(conn)
    b.poll()
    stream = b''
    for i in range(3):
        line = '$GPGGA,%d*00\r\n' % i
        stream += line.encode()
        b.send(line)
    assert stream.startswith(conn.sent)              # what arrived is an exact, gap-free prefix of the stream
    assert len(conn.sent) == 9 and not conn.closed   # 3 calls x 3 bytes, the rest is queued, not lost
    conn.mode = 'ok'                                 # the client recovers: the backlog is flushed first
    b.send('$LAST*00\r\n')
    assert conn.sent == stream + b'$LAST*00\r\n'


def test_client_that_never_catches_up_is_dropped_on_backlog_or_strikes():
    b, net, sock, tcp, udp = started()
    conn = FakeConn('partial')
    tcp.pending.append(conn)
    b.poll()
    for _ in range(wifi.STRIKES_MAX):
        b.send('$X*00\r\n')
    assert conn.closed and b.client_count == 0
    big = FakeConn('eagain')
    tcp.pending.append(big)
    b.poll()
    b.send('x' * (wifi.PENDING_MAX + 1))             # a single batch larger than the cap
    assert big.closed


def test_stop_closes_everything_and_send_is_noop():
    b, net, sock, tcp, udp = started()
    conn = FakeConn()
    tcp.pending.append(conn)
    b.poll()
    b.stop()
    assert conn.closed and tcp.closed and udp.closed
    assert not net.wlan.is_active and not b.active
    assert b.info()[0] == 'OFF'
    b.send('$X*00\r\n')          # must not raise
    b.poll()
    assert udp.datagrams == []


def test_udp_error_does_not_raise():
    b, net, sock, tcp, udp = started()
    def boom(data, addr):
        raise OSError(113, 'EHOSTUNREACH')
    udp.sendto = boom
    b.send('$X*00\r\n')


def test_a_build_without_the_security_option_still_starts():
    b, net, sock = make()
    original = net.wlan.config

    def config(**kw):
        if 'security' in kw:
            raise ValueError('unknown config param')
        original(**kw)
    net.wlan.config = config
    b.start()
    assert b.active and 'security' not in net.wlan.cfg


def test_country_and_channel_are_applied_before_the_ap_comes_up():
    net, sock = FakeNetwork(), FakeSocketModule()
    b = NmeaBroadcaster(net, sock, 'x', 'secret123', country='SE', channel=6)
    b.start()
    assert net.country_set == 'SE' and net.wlan.cfg['channel'] == 6


def test_no_country_or_channel_leaves_the_firmware_defaults():
    b, net, sock = make()
    b.start()
    assert not hasattr(net, 'country_set') and 'channel' not in net.wlan.cfg


def test_associated_phones_are_counted_for_the_display():
    b, net, sock = make()
    b.start()
    net.wlan.status = lambda what=None: [b'\x01' * 6, b'\x02' * 6]
    assert b.stations() == 2 and b.info()[0] == 'ON sta2'
