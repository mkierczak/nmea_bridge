"""Page navigation, button handling and key events for the two-key UI. Pure logic, no hardware."""

try:
    from utime import ticks_diff as _ticks_diff
except ImportError:
    def _ticks_diff(a, b):
        return a - b

(PAGE_MAIN, PAGE_STATS, PAGE_SATS, PAGE_SIGNAL, PAGE_SPOOF, PAGE_SYSTEM, PAGE_DEBUG,
 PAGE_WIFI, PAGE_SPEED, PAGE_GPS, PAGE_LOG, PAGE_ANCHOR, PAGE_MOB) = range(13)
# The main loop is what you look at under way; the debug loop (both keys held for 2 s) has the details.
MAIN_PAGES = (PAGE_MAIN, PAGE_SPEED, PAGE_GPS, PAGE_ANCHOR, PAGE_MOB, PAGE_WIFI)   # MOB and Wi-Fi only while they apply
DEBUG_PAGES = (PAGE_LOG, PAGE_STATS, PAGE_SATS, PAGE_SIGNAL, PAGE_SPOOF, PAGE_SYSTEM, PAGE_DEBUG)
PAGES = MAIN_PAGES + DEBUG_PAGES

UP_SHORT, UP_LONG, DN_SHORT, DN_LONG, WIFI, CHORD, MOB = ('UP_SHORT', 'UP_LONG', 'DN_SHORT', 'DN_LONG', 'WIFI', 'CHORD',
                                                         'MOB')

LONG_MS = 1000
WIFI_MS = 3000
MOB_MS = 3000                 # DN held this long: man overboard (the position is marked)
CHORD_MS = 2000               # both keys held this long: switch between the main loop and the debug loop
DEBOUNCE_MS = 30

# What Navigator.handle() asks the main loop to do
OPEN_MENU, TOGGLE_WIFI, TO_MENU, PAGE_ACTION = 'open_menu', 'toggle_wifi', 'menu', 'page_action'


def classify(key, held_ms, long_ms=LONG_MS, wifi_ms=WIFI_MS, mob_ms=None):
    """Event for a key released after 'held_ms' ('UP' or 'DN'); wifi_ms=None disables the Wi-Fi gesture (UP held
    long), mob_ms=None the man-overboard gesture (DN held long)."""
    if key == 'UP':
        if wifi_ms is not None and held_ms >= wifi_ms:
            return WIFI
        return UP_LONG if held_ms >= long_ms else UP_SHORT
    if mob_ms is not None and held_ms >= mob_ms:
        return MOB
    return DN_LONG if held_ms >= long_ms else DN_SHORT


class ButtonTracker(object):
    """Turns raw edges of one active-low button into events: debounced, press and release paired.

    Call edge(value, now_ms) from the pin IRQ (value = pin.value()); it returns an event or None."""

    def __init__(self, name, long_ms=LONG_MS, wifi_ms=WIFI_MS, debounce_ms=DEBOUNCE_MS, mob_ms=None):
        self.name = name
        self.long_ms = long_ms
        self.wifi_ms = wifi_ms
        self.mob_ms = mob_ms
        self.debounce_ms = debounce_ms
        self.partner = None          # the other key; while both are down neither produces a single-key event
        self.chord = False           # this press overlapped the other key
        self.fired = False           # ... and the chord event was already sent
        self._pressed_at = None
        self._last_edge = None

    def held_ms(self, now_ms):
        """How long the key has been held down on its own, or None while it is not pressed (or is part of a
        chord)."""
        pressed = self._pressed_at
        return None if pressed is None or self.chord else _ticks_diff(now_ms, pressed)

    def edge(self, value, now_ms):
        if self._last_edge is not None and _ticks_diff(now_ms, self._last_edge) < self.debounce_ms:
            return None                      # contact bounce
        self._last_edge = now_ms
        if value == 0:                       # pressed (active low)
            if self._pressed_at is None:
                self._pressed_at = now_ms
                other = self.partner
                if other is not None and other._pressed_at is not None:
                    self.chord = other.chord = True     # both down: this is no single-key press
                    self.fired = other.fired = False
            return None
        if self._pressed_at is None:         # a release we never saw the press of: ignore
            return None
        held = _ticks_diff(now_ms, self._pressed_at)
        self._pressed_at = None
        if self.chord:
            other = self.partner
            if other is None or other._pressed_at is None:     # the last one let go: the chord is over
                self.chord = self.fired = False
                if other is not None:
                    other.chord = other.fired = False
            return None                      # a key that was part of a chord never acts alone
        return classify(self.name, held, self.long_ms, self.wifi_ms, self.mob_ms)


def pair(up, dn):
    """Make two trackers aware of each other so that holding both is a chord, not two single presses."""
    up.partner, dn.partner = dn, up


def chord_held_ms(up, dn, now_ms):
    """How long both keys have been down together (None unless both are, or after the chord event fired)."""
    if up._pressed_at is None or dn._pressed_at is None or up.fired:
        return None
    return min(_ticks_diff(now_ms, up._pressed_at), _ticks_diff(now_ms, dn._pressed_at))


def chord_due(up, dn, now_ms, chord_ms=CHORD_MS):
    """True once, when both keys have been held for chord_ms. Call from the main loop; put CHORD on the event
    queue when it returns True."""
    held = chord_held_ms(up, dn, now_ms)
    if held is not None and held >= chord_ms:
        up.fired = dn.fired = True
        return True
    return False


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
    """Which page is shown. Two loops of pages: the main loop (Main, Speed, GPS and, while the access point is
    up, Wi-Fi) and the debug loop (everything with details), switched by the CHORD event."""

    def __init__(self, wifi=True):
        self.wifi = wifi                 # the build has the Wi-Fi page at all
        self.wifi_up = False             # the access point is on (or failed): the page is worth showing
        self.mob_active = False          # a man-overboard position is marked: its page is in the loop
        self.debug = False
        self.page = PAGE_MAIN
        self.in_menu = False

    @property
    def pages(self):
        if self.debug:
            return DEBUG_PAGES
        return tuple(p for p in MAIN_PAGES
                     if (p != PAGE_MOB or self.mob_active) and (p != PAGE_WIFI or (self.wifi and self.wifi_up)))

    def check(self):
        """Back to the first page of the loop when the current page no longer exists (Wi-Fi switched off)."""
        if self.page not in self.pages:
            self.page = self.pages[0]

    def leave_debug(self):
        if self.debug:
            self.debug = False
            self.page = PAGE_MAIN

    def normalize(self, event):
        """Inside the menu the Wi-Fi gesture (UP held 3 s) is just a long UP: a slightly long confirm must
        not toggle the access point. Call before handle() and forward the returned event to the menu."""
        if event == WIFI and self.in_menu:
            return UP_LONG
        return event

    def handle(self, event):
        """Update state for one (normalized) event. Returns None, OPEN_MENU, TOGGLE_WIFI or TO_MENU
        (forward the event to the menu, which tells us when to close it via close_menu())."""
        if event == CHORD:
            if not self.in_menu:             # both keys held: the other loop (not while the menu is open)
                self.debug = not self.debug
                self.page = DEBUG_PAGES[0] if self.debug else PAGE_MAIN
            return None
        if event == WIFI:                        # UP held for 3 s
            if self.page in (PAGE_ANCHOR, PAGE_MOB) and not self.debug:
                return PAGE_ACTION                   # there it drops / lifts the anchor, lifts the mark
            return TOGGLE_WIFI if self.wifi else None
        if self.in_menu:
            return TO_MENU
        if event == UP_SHORT:
            self._step(1)
        elif event == DN_SHORT:
            self._step(-1)
        elif event == DN_LONG:
            if self.debug:                           # a long DOWN leaves the debug loop
                self.leave_debug()
            elif self.page == PAGE_MAIN:             # on the top (Main) page it opens the menu
                self.in_menu = True
                return OPEN_MENU
            else:
                self.page = PAGE_MAIN                # anywhere else it goes back to the Main page
        return None                                  # (a long UP has no function on the pages)

    def close_menu(self):
        self.in_menu = False

    def _step(self, direction):
        pages = self.pages
        if self.page in pages:
            self.page = pages[(pages.index(self.page) + direction) % len(pages)]
        else:
            self.page = pages[0]
