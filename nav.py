"""Page navigation, button handling and key events for the two-key UI. Pure logic, no hardware."""

try:
    from utime import ticks_diff as _ticks_diff
except ImportError:
    def _ticks_diff(a, b):
        return a - b

(PAGE_MAIN, PAGE_STATS, PAGE_SATS, PAGE_SIGNAL, PAGE_SPOOF, PAGE_SYSTEM, PAGE_DEBUG,
 PAGE_WIFI) = range(8)
PAGES = (PAGE_MAIN, PAGE_STATS, PAGE_SATS, PAGE_SIGNAL, PAGE_SPOOF, PAGE_SYSTEM, PAGE_DEBUG, PAGE_WIFI)

UP_SHORT, UP_LONG, DN_SHORT, DN_LONG, WIFI = 'UP_SHORT', 'UP_LONG', 'DN_SHORT', 'DN_LONG', 'WIFI'

LONG_MS = 1000
WIFI_MS = 3000
DEBOUNCE_MS = 30

# What Navigator.handle() asks the main loop to do
OPEN_MENU, TOGGLE_WIFI, TO_MENU = 'open_menu', 'toggle_wifi', 'menu'


def classify(key, held_ms, long_ms=LONG_MS, wifi_ms=WIFI_MS):
    """Event for a key released after 'held_ms' ('UP' or 'DN'); wifi_ms=None disables the Wi-Fi gesture."""
    if key == 'UP':
        if wifi_ms is not None and held_ms >= wifi_ms:
            return WIFI
        return UP_LONG if held_ms >= long_ms else UP_SHORT
    return DN_LONG if held_ms >= long_ms else DN_SHORT


class ButtonTracker(object):
    """Turns raw edges of one active-low button into events: debounced, press and release paired.

    Call edge(value, now_ms) from the pin IRQ (value = pin.value()); it returns an event or None."""

    def __init__(self, name, long_ms=LONG_MS, wifi_ms=WIFI_MS, debounce_ms=DEBOUNCE_MS):
        self.name = name
        self.long_ms = long_ms
        self.wifi_ms = wifi_ms
        self.debounce_ms = debounce_ms
        self._pressed_at = None
        self._last_edge = None

    def edge(self, value, now_ms):
        if self._last_edge is not None and _ticks_diff(now_ms, self._last_edge) < self.debounce_ms:
            return None                      # contact bounce
        self._last_edge = now_ms
        if value == 0:                       # pressed (active low)
            if self._pressed_at is None:
                self._pressed_at = now_ms
            return None
        if self._pressed_at is None:         # a release we never saw the press of: ignore
            return None
        held = _ticks_diff(now_ms, self._pressed_at)
        self._pressed_at = None
        return classify(self.name, held, self.long_ms, self.wifi_ms)


class EventQueue(object):
    """Fixed-size ring for key events: one producer (the pin IRQ) and one consumer (the main loop).

    Each side only writes its own index, so no lock is needed and nothing is lost to a copy-then-clear race."""

    def __init__(self, size=8):
        self._buf = [None] * size
        self._size = size
        self._head = 0   # next to read (consumer)
        self._tail = 0   # next to write (producer)

    def put(self, event):
        nxt = (self._tail + 1) % self._size
        if nxt == self._head:
            return False                     # full: drop the newest
        self._buf[self._tail] = event
        self._tail = nxt
        return True

    def get(self):
        if self._head == self._tail:
            return None
        event = self._buf[self._head]
        self._buf[self._head] = None
        self._head = (self._head + 1) % self._size
        return event

    def drain(self):
        out = []
        while True:
            event = self.get()
            if event is None:
                return out
            out.append(event)


class Navigator(object):

    def __init__(self, pages=PAGES):
        self.pages = pages
        self.page = pages[0]
        self.in_menu = False

    def normalize(self, event):
        """Inside the menu the Wi-Fi gesture (UP held 3 s) is just a long UP: a slightly long confirm must
        not toggle the access point. Call before handle() and forward the returned event to the menu."""
        if event == WIFI and self.in_menu:
            return UP_LONG
        return event

    def handle(self, event):
        """Update state for one (normalized) event. Returns None, OPEN_MENU, TOGGLE_WIFI or TO_MENU
        (forward the event to the menu, which tells us when to close it via close_menu())."""
        if event == WIFI:
            return TOGGLE_WIFI
        if self.in_menu:
            return TO_MENU
        if event == UP_SHORT:
            self._step(1)
        elif event == DN_SHORT:
            self._step(-1)
        elif event == DN_LONG:
            if self.page == self.pages[0]:       # on the top (Main) page a long DOWN opens the menu
                self.in_menu = True
                return OPEN_MENU
            self.page = self.pages[0]            # anywhere else it goes back to the Main page
        return None                              # (a long UP has no function on the pages)

    def close_menu(self):
        self.in_menu = False

    def _step(self, direction):
        if self.page in self.pages:
            self.page = self.pages[(self.pages.index(self.page) + direction) % len(self.pages)]
        else:
            self.page = self.pages[0]
