"""User-interface controller: key events, menu, screen-off timer and redrawing.

Free of hardware imports (the oled and everything else is injected) so it can be tested on a desktop.
Uptime and idle time are accumulated from the (small) differences between consecutive steps, so they
stay correct after MicroPython's millisecond counter wraps, which `ticks_diff` over days cannot do.
"""
import gc

import nav

try:
    from utime import ticks_diff
except ImportError:
    def ticks_diff(a, b):
        return a - b

MENU_TIMEOUT_MS = 60 * 1000
REFRESH_MS = 500


class UiController(object):

    def __init__(self, cfg, oled, font, draw, navigator, events, bridge, menu_factory,
                 wifi_info, system_info, wifi_toggle, clock,
                 menu_timeout_ms=MENU_TIMEOUT_MS, refresh_ms=REFRESH_MS):
        self.cfg = cfg
        self.oled = oled
        self.font = font
        self.draw = draw                      # screens.draw
        self.navigator = navigator
        self.events = events                  # nav.EventQueue filled by the button IRQs
        self.bridge = bridge
        self.menu_factory = menu_factory      # () -> menu.Menu (imports lazily)
        self.wifi_info = wifi_info
        self.system_info = system_info
        self.wifi_toggle = wifi_toggle
        self.menu_timeout_ms = menu_timeout_ms
        self.refresh_ms = refresh_ms
        self.menu = None
        self.screen_off = False
        self.force_draw = True
        self.uptime_ms = 0
        self.idle_ms = 0                      # time since the last key press (menu timeout)
        self.screen_idle_ms = 0               # time since a key press or an active alert (screen-off timer)
        now = clock()
        self._last_tick = now
        self._last_draw = now
        self._last_sig = None

    def step(self, now):
        self._advance(now)
        for ev in self.events.drain():
            self._handle(ev)
        if self.menu is not None and self.idle_ms > self.menu_timeout_ms:
            self._close_menu(cancel=True)     # an unconfirmed edit is reverted, not left half-applied
            self.force_draw = True
        # the screen-off timer; an active jamming/spoofing alert always wakes the display and keeps it on
        alert = self.bridge.alert()
        off_s = self.cfg.get('screen_off_s')
        if alert:
            self.screen_idle_ms = 0           # an alert counts as activity: the screen stays on after it clears
            if self.screen_off:
                self._wake()
        elif off_s and not self.screen_off and self.screen_idle_ms > off_s * 1000:
            self.screen_off = True
            self.oled.sleep(True)
        self._draw(now)

    # --- internals ------------------------------------------------------------------------
    def _advance(self, now):
        dt = ticks_diff(now, self._last_tick)
        self._last_tick = now
        if dt > 0:
            self.uptime_ms += dt
            self.idle_ms += dt
            self.screen_idle_ms += dt

    def _wake(self):
        self.screen_off = False
        self.oled.sleep(False)
        self.force_draw = True
        self._last_sig = None

    def _handle(self, ev):
        self.idle_ms = 0
        self.screen_idle_ms = 0
        self.force_draw = True
        if self.screen_off:
            self._wake()                      # the press that wakes the display does nothing else
            return
        ev = self.navigator.normalize(ev)
        action = self.navigator.handle(ev)
        if action == nav.OPEN_MENU:
            self.menu = self.menu_factory()
        elif action == nav.TO_MENU:
            if self.menu is None or self.menu.handle(ev) == 'close':
                self._close_menu()
        elif action == nav.TOGGLE_WIFI:
            self.wifi_toggle()
            self._last_sig = None

    def _close_menu(self, cancel=False):
        if cancel and self.menu is not None:
            self.menu.cancel()
        self.navigator.close_menu()
        self.menu = None
        gc.collect()

    def _draw(self, now):
        if self.screen_off:
            return
        if not (self.force_draw or ticks_diff(now, self._last_draw) > self.refresh_ms):
            return
        self._last_draw = now
        redraw_all = self.force_draw
        self.force_draw = False
        if self.menu is not None:
            self.menu.draw(self.oled)
            self.oled.show()
            return
        bridge = self.bridge
        parser = bridge.parser
        page = self.navigator.page
        no_fix = bridge.no_fix(now)
        info = self.system_info() if page in (nav.PAGE_SYSTEM, nav.PAGE_SPOOF) else None
        sig = (page, no_fix, bridge.queue.dropped, tuple(bridge.stats.values()),
               parser.display_signature(),
               bridge.detector.signature() if bridge.detector else None,
               bridge.spoof.signature() if bridge.spoof else None,
               self.wifi_info() if page == nav.PAGE_WIFI else None,
               tuple(info.values()) if info else None)
        if redraw_all or sig != self._last_sig:
            self.draw(self.oled, self.font, page, parser, bridge.stats, bridge.queue.dropped, no_fix,
                      bridge.detector, bridge.spoof, self.wifi_info(), info)
            self.oled.show()
            self._last_sig = sig
