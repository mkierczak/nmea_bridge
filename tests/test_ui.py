import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import NMEA
import nav
import settings as S
import ui
from bridge import Bridge, RxQueue
from menu import Menu
from nav import UP_SHORT, UP_LONG, DN_SHORT, DN_LONG, WIFI
from test_bridge import Clock, FakeDetector, FakeRadio
from test_jamming import with_checksum
from test_menu import FakeOled
from test_settings import full_defaults


class UiOled(FakeOled):
    def __init__(self):
        super().__init__()
        self.shows = 0
        self.sleeps = []

    def show(self):
        self.shows += 1

    def sleep(self, value=True):
        self.sleeps.append(value)


class Rig:
    def __init__(self, screen_off_s=0, menu_timeout_ms=60000):
        self.dir = tempfile.TemporaryDirectory()
        self.cfg = S.Settings(full_defaults(), os.path.join(self.dir.name, 'settings.json'))
        if screen_off_s:
            self.cfg.set('screen_off_s', screen_off_s)
        self.clock = Clock(1000)
        self.bridge = Bridge(NMEA.Parser(), RxQueue(16), FakeRadio(), self.clock)
        self.oled = UiOled()
        self.events = nav.EventQueue(8)
        self.navigator = nav.Navigator()
        self.draws = []
        self.toggles = 0
        self.system_calls = 0
        self.hook_log = []

        def draw(oled, font, page, parser, stats, dropped, no_fix, jam, spoof, wifi, info):
            self.draws.append((page, info is not None))
        self.hooks = {'apply': lambda k: self.hook_log.append(k), 'reset': lambda: None,
                      'reboot': lambda: self.hook_log.append('reboot'),
                      'wifi_active': lambda: False, 'wifi_toggle': lambda: None}

        def system_info():
            self.system_calls += 1
            return {'uptime_s': 0, 'now_ms': self.clock.now}
        self.ui = ui.UiController(
            self.cfg, self.oled, None, draw, self.navigator, self.events, self.bridge,
            lambda: Menu(self.cfg, self.hooks), lambda: ('OFF', 'x', '', 0, 4, 'pw'), system_info,
            lambda: self._toggle(), self.clock, menu_timeout_ms=menu_timeout_ms)

    def _toggle(self):
        self.toggles += 1

    def step(self, advance=0):
        self.clock.now += advance
        self.ui.step(self.clock.now)

    def press(self, *evs):
        for ev in evs:
            self.events.put(ev)
        self.step()


def test_first_step_draws_once_and_idle_steps_do_not_redraw():
    r = Rig()
    r.step()
    assert r.draws == [(nav.PAGE_MAIN, False)] and r.oled.shows == 1
    for _ in range(5):
        r.step(advance=ui.REFRESH_MS + 10)                 # refresh time passes, nothing visible changed
    assert len(r.draws) == 1


def test_a_key_press_redraws_at_once_and_pages_cycle():
    r = Rig()
    r.step()
    r.press(UP_SHORT)
    assert r.navigator.page == nav.PAGE_STATS and r.draws[-1][0] == nav.PAGE_STATS
    r.press(DN_SHORT, DN_SHORT)
    assert r.navigator.page == nav.PAGES[-1]
    r.press(DN_LONG)
    assert r.navigator.page == nav.PAGE_MAIN


def test_system_info_is_only_gathered_for_the_pages_that_need_it():
    r = Rig()
    r.step()
    assert r.system_calls == 0
    r.navigator.page = nav.PAGE_SYSTEM
    r.press(UP_SHORT, DN_SHORT)                            # back to the system page via events
    assert r.system_calls >= 1 and r.draws[-1] == (nav.PAGE_SYSTEM, True)


def test_menu_opens_and_closes_with_long_presses():
    r = Rig()
    r.press(UP_LONG)
    assert r.ui.menu is not None and r.navigator.in_menu
    assert r.oled.texts()[0] == 'Menu'                     # the menu drew itself
    r.press(DN_LONG)                                       # back at the top level: leave
    assert r.ui.menu is None and not r.navigator.in_menu


def test_menu_timeout_reverts_an_unconfirmed_edit_and_closes():
    r = Rig(menu_timeout_ms=60000)
    r.press(UP_LONG)
    for _ in range(3):
        r.press(DN_SHORT)                                  # root: GPS, Detection, Radio output, Display
    r.press(UP_LONG)                                       # into Display
    r.press(UP_LONG)                                       # edit Contrast
    r.press(UP_SHORT, UP_SHORT)
    assert r.cfg.get('contrast') == 30 and r.hook_log.count('contrast') == 2
    r.step(advance=61000)                                  # nobody touches the keys
    assert r.ui.menu is None and not r.navigator.in_menu
    assert r.cfg.get('contrast') == 0                      # not left half-applied
    assert r.hook_log[-1] == 'contrast' and r.cfg.overrides() == {}


def test_wifi_gesture_toggles_outside_the_menu_but_only_confirms_inside_it():
    r = Rig()
    r.press(WIFI)
    assert r.toggles == 1
    r.press(UP_LONG)                                       # open the menu
    r.press(WIFI)                                          # a slightly long hold on "GPS"
    assert r.toggles == 1 and r.ui.menu.name == 'GPS'      # it entered the submenu like a normal confirm


def test_screen_off_timer_wake_key_is_swallowed_and_alert_keeps_the_screen_on():
    r = Rig(screen_off_s=30)
    r.step()
    r.step(advance=29000)
    assert r.oled.sleeps == []
    r.step(advance=2000)
    assert r.oled.sleeps == [True] and r.ui.screen_off
    shows = r.oled.shows
    r.step(advance=1000)
    assert r.oled.shows == shows                           # nothing is drawn while off
    page = r.navigator.page
    r.press(UP_SHORT)                                      # first press only wakes the display
    assert not r.ui.screen_off and r.oled.sleeps == [True, False] and r.navigator.page == page
    r.press(UP_SHORT)
    assert r.navigator.page != page                        # the next one acts
    # an alert wakes it and keeps it on, however long nobody presses a key
    r.step(advance=40000)
    assert r.ui.screen_off
    r.bridge.spoof = FakeDetector('ALERT')
    r.step(advance=1000)
    assert not r.ui.screen_off
    r.step(advance=120000)
    assert not r.ui.screen_off
    r.bridge.spoof.state = 'OK'
    r.step(advance=1000)
    assert not r.ui.screen_off                             # grace period after the alert
    r.step(advance=31000)
    assert r.ui.screen_off


def test_uptime_and_idle_clocks_survive_the_tick_wrap():
    saved = ui.ticks_diff
    ui.ticks_diff = lambda a, b: ((a - b + (1 << 29)) & ((1 << 30) - 1)) - (1 << 29)
    try:
        r = Rig(screen_off_s=60)
        r.clock.now = (1 << 30) - 1000
        r.ui._last_tick = r.clock.now
        r.step()
        r.clock.now = 500                                  # wrapped: 1500 ms later
        r.ui.step(r.clock.now)
        assert r.ui.uptime_ms == 1500 and r.ui.idle_ms == 1500
        for _ in range(100):                               # days of uptime in big steps would break ticks_diff
            r.clock.now = (r.clock.now + 400000) % (1 << 30)
            r.ui.step(r.clock.now)
        assert r.ui.uptime_ms == 1500 + 100 * 400000       # monotonic, never negative
        assert r.ui.screen_off                             # the screen-off timer kept working
    finally:
        ui.ticks_diff = saved


def test_unknown_menu_state_does_not_crash_the_loop():
    r = Rig()
    r.navigator.in_menu = True                             # inconsistent state: menu object missing
    r.press(UP_SHORT)
    assert not r.navigator.in_menu and r.ui.menu is None


def test_pages_are_not_redrawn_when_only_the_last_sentence_changes():
    r = Rig()
    r.step()
    n = len(r.draws)
    r.bridge.parser.parse_sentence(with_checksum('GPTXT,01,01,02,ANTENNA OK'))   # nothing the main page shows
    r.step(advance=ui.REFRESH_MS + 10)
    assert len(r.draws) == n                                  # used to redraw every 500 ms regardless
    r.press(UP_SHORT)                                         # the Stats page lists the last-seen types
    n = len(r.draws)
    r.bridge.parser.parse_sentence(with_checksum('GPZDA,201530.00,04,07,2002,00,00'))
    r.step(advance=ui.REFRESH_MS + 10)
    assert len(r.draws) == n + 1
    r.step(advance=ui.REFRESH_MS + 10)
    assert len(r.draws) == n + 1                              # and stays quiet again
    r.bridge.parser.parse_sentence(with_checksum('GNGGA,123520,4807.040,N,01131.000,E,1,08,0.9,5.4,M,46.9,M,,'))
    r.step(advance=ui.REFRESH_MS + 10)
    assert len(r.draws) == n + 2                              # visible data changed: redrawn
