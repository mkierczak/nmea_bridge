"""Page navigation and key-press classification for the two-key UI. Pure logic, no hardware."""

(PAGE_MAIN, PAGE_STATS, PAGE_SATS, PAGE_SIGNAL, PAGE_SPOOF, PAGE_SYSTEM, PAGE_DEBUG,
 PAGE_WIFI) = range(8)
PAGES = (PAGE_MAIN, PAGE_STATS, PAGE_SATS, PAGE_SIGNAL, PAGE_SPOOF, PAGE_SYSTEM, PAGE_DEBUG, PAGE_WIFI)

UP_SHORT, UP_LONG, DN_SHORT, DN_LONG, WIFI = 'UP_SHORT', 'UP_LONG', 'DN_SHORT', 'DN_LONG', 'WIFI'

LONG_MS = 1000
WIFI_MS = 3000

# What Navigator.handle() asks the main loop to do
OPEN_MENU, TOGGLE_WIFI, TO_MENU = 'open_menu', 'toggle_wifi', 'menu'


def classify(key, held_ms, long_ms=LONG_MS, wifi_ms=WIFI_MS):
    """Event for a key released after 'held_ms' ('UP' or 'DN'); wifi_ms=None disables the Wi-Fi gesture."""
    if key == 'UP':
        if wifi_ms is not None and held_ms >= wifi_ms:
            return WIFI
        return UP_LONG if held_ms >= long_ms else UP_SHORT
    return DN_LONG if held_ms >= long_ms else DN_SHORT


class Navigator(object):

    def __init__(self, pages=PAGES):
        self.pages = pages
        self.page = pages[0]
        self.in_menu = False

    def handle(self, event):
        """Update state for one event. Returns None, OPEN_MENU, TOGGLE_WIFI or TO_MENU (forward the
        event to the menu, which tells us when to close it via close_menu())."""
        if event == WIFI:
            return TOGGLE_WIFI
        if self.in_menu:
            return TO_MENU
        if event == UP_SHORT:
            self._step(1)
        elif event == DN_SHORT:
            self._step(-1)
        elif event == DN_LONG:
            self.page = self.pages[0]
        elif event == UP_LONG:
            self.in_menu = True
            return OPEN_MENU
        return None

    def close_menu(self):
        self.in_menu = False

    def _step(self, direction):
        if self.page in self.pages:
            self.page = self.pages[(self.pages.index(self.page) + direction) % len(self.pages)]
        else:
            self.page = self.pages[0]
