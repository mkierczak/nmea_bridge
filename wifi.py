"""Wi-Fi access point that serves the NMEA stream over TCP and UDP broadcast (Pico W).

Everything is non-blocking so the main loop never waits on a slow client. `network` and `socket`
are injected so the logic can be tested on a desktop; call all methods from the main thread only
(MicroPython's network stack is not safe to use from the second core).
"""

MAX_UDP_PAYLOAD = 1400   # keep datagrams under a typical MTU; split on line boundaries
STRIKES_MAX = 5          # consecutive "buffer full" results before a slow TCP client is dropped
PENDING_MAX = 2048       # bytes queued for one slow client before it is dropped
_EAGAIN = 11
SECURITY_WPA2_AES = 0x00400004   # CYW43_AUTH_WPA2_AES_PSK: plain WPA2-PSK, which every phone accepts


def broadcast_address(ip, mask):
    """'192.168.4.1', '255.255.255.0' -> '192.168.4.255'."""
    ip_b = [int(x) for x in ip.split('.')]
    mask_b = [int(x) for x in mask.split('.')]
    return '.'.join(str((i | (~m & 0xFF)) & 0xFF) for i, m in zip(ip_b, mask_b))


def _errno(exc):
    return exc.args[0] if exc.args else None


def chunk_lines(data, limit=MAX_UDP_PAYLOAD):
    """Split bytes into pieces of at most 'limit' bytes, breaking after a newline where possible."""
    out = []
    while len(data) > limit:
        cut = data.rfind(b'\n', 0, limit) + 1 or limit
        out.append(data[:cut])
        data = data[cut:]
    if data:
        out.append(data)
    return out


class NmeaBroadcaster(object):

    def __init__(self, network, socket, ssid, password, port=10110, max_clients=4, country='', channel=None):
        self._network = network
        self._socket = socket
        self.ssid = ssid
        self._password = password
        self.port = port
        self.max_clients = max_clients
        self.country = country
        self.channel = channel
        self.active = False
        self.error = ''
        self.ip = ''
        self.bytes_sent = 0
        self._wlan = None
        self._listener = None
        self._udp = None
        self._bcast = None
        self._clients = []          # [connection, strikes, pending bytes not yet accepted]

    def set_password(self, password):
        """Use a new password the next time the AP is started."""
        self._password = password

    @property
    def client_count(self):
        return len(self._clients)

    def stations(self):
        """Phones associated with the access point (Wi-Fi level, before any TCP connection): shows whether
        a join attempt gets as far as the radio."""
        try:
            return len(self._wlan.status('stations'))
        except (AttributeError, ValueError, OSError, TypeError):
            return 0

    def info(self):
        """(state, ssid, ip, clients, max_clients) for the display."""
        state = 'ERR' if self.error else 'ON sta{}'.format(self.stations()) if self.active else 'OFF'
        return state, self.ssid, self.ip, len(self._clients), self.max_clients

    def start(self, feed=None, wait_ms=5000, sleep_ms=None):
        """Bring the AP up (WPA2) and open the sockets. 'feed' (e.g. wdt.feed) is called while waiting.

        Raises ValueError for a password that WPA2 would reject (no open networks by design)."""
        if len(self._password) < 8:
            raise ValueError('WIFI_PASSWORD must have at least 8 characters (WPA2)')
        self.error = ''
        try:
            net, sock = self._network, self._socket
            if feed:
                feed()
            if self.country:    # the default 'XX' (worldwide) regulatory domain made an Android phone fail to join
                try:
                    net.country(self.country)
                except (ValueError, OSError, AttributeError):
                    pass
            wlan = net.WLAN(net.AP_IF)
            wlan.config(essid=self.ssid, password=self._password)
            if self.channel:
                try:
                    wlan.config(channel=self.channel)
                except (ValueError, OSError, TypeError):
                    pass
            try:                # do not rely on the firmware's default (newer builds offer WPA2/WPA3 mixed mode,
                wlan.config(security=SECURITY_WPA2_AES)   # which some Android phones refuse at once)
            except (ValueError, OSError, TypeError):
                pass            # a build without the option keeps its default
            wlan.active(True)
            waited = 0
            while not wlan.active() and waited < wait_ms:
                if sleep_ms:
                    sleep_ms(100)
                waited += 100
                if feed:
                    feed()
            if not wlan.active():
                raise OSError('access point did not come up')
            self._wlan = wlan
            ip, mask = wlan.ifconfig()[0:2]
            self.ip = ip
            self._bcast = broadcast_address(ip, mask)

            listener = sock.socket(sock.AF_INET, sock.SOCK_STREAM)
            self._listener = listener
            listener.setsockopt(sock.SOL_SOCKET, sock.SO_REUSEADDR, 1)
            listener.bind(('0.0.0.0', self.port))
            listener.listen(2)
            listener.setblocking(False)

            udp = sock.socket(sock.AF_INET, sock.SOCK_DGRAM)
            self._udp = udp
            opt = getattr(sock, 'SO_BROADCAST', None)  # not exposed by every MicroPython build
            if opt is not None:
                try:
                    udp.setsockopt(sock.SOL_SOCKET, opt, 1)
                except OSError:
                    pass
            self.active = True
            if feed:
                feed()
        except Exception as e:
            self.stop()
            self.error = str(e)[:20] or 'error'
            raise

    def stop(self):
        for entry in self._clients:
            self._close(entry[0])
        self._clients = []
        for s in (self._listener, self._udp):
            if s is not None:
                self._close(s)
        self._listener = self._udp = None
        if self._wlan is not None:
            try:
                self._wlan.active(False)  # saves power and RF noise next to the GNSS antenna
            except OSError:
                pass
        self._wlan = None
        self.active = False
        self.ip = ''

    @staticmethod
    def _close(sock):
        try:
            sock.close()
        except OSError:
            pass

    def poll(self):
        """Accept pending TCP connections (up to max_clients; extras are closed)."""
        if not self.active:
            return
        for _ in range(self.max_clients + 1):
            try:
                conn, _addr = self._listener.accept()
            except OSError:
                return  # nothing waiting
            if len(self._clients) >= self.max_clients:
                self._close(conn)
                continue
            try:
                conn.setblocking(False)
            except OSError:
                self._close(conn)
                continue
            self._clients.append([conn, 0, b''])

    def send(self, text):
        """Send one batch (str or bytes, whole NMEA lines) to all TCP clients and as UDP broadcast."""
        if not self.active or not text:
            return
        data = text.encode() if isinstance(text, str) else text
        alive = []
        for entry in self._clients:
            conn = entry[0]
            out = entry[2] + data           # bytes the client has not taken yet go first, in order
            try:
                n = conn.send(out)
                if n is None:
                    n = 0
            except OSError as e:
                if _errno(e) != _EAGAIN:    # reset, broken pipe, ...
                    self._close(conn)
                    continue
                n = 0
            self.bytes_sent += n
            entry[2] = out[n:]              # never drop the tail: a client must not see a cut sentence
            entry[1] = entry[1] + 1 if entry[2] else 0
            if entry[1] >= STRIKES_MAX or len(entry[2]) > PENDING_MAX:
                self._close(conn)
                continue
            alive.append(entry)
        self._clients = alive
        for piece in chunk_lines(data):
            try:
                self._udp.sendto(piece, (self._bcast, self.port))
            except OSError:
                break  # no route / buffers busy: lossy by design
