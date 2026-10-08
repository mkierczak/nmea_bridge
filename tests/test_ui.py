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
        self.inverts = []

    def invert(self, flag):
        self.inverts.append(flag)

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
        self.ctxs = []
        self.wifi_args = []
        self.toggles = 0
        self.system_calls = 0
        self.hook_log = []

        def draw(oled, font, page, parser, stats, dropped, no_fix, jam, spoof, wifi, info, ctx=None):
            self.draws.append((page, info is not None))
            self.ctxs.append(ctx)
            self.wifi_args.append(wifi)
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
    r.bridge.parser.fix_type, r.bridge.last_pos = 'GPS', r.clock.now      # a fix: nothing on the page ticks
    r.step()
    assert r.draws == [(nav.PAGE_MAIN, False)] and r.oled.shows == 1
    for _ in range(5):
        r.step(advance=ui.REFRESH_MS + 10)                 # refresh time passes, nothing visible changed
    assert len(r.draws) == 1


def test_a_key_press_redraws_at_once_and_pages_cycle():
    r = Rig()
    r.step()
    r.press(UP_SHORT)
    assert r.navigator.page == nav.PAGE_SPEED and r.draws[-1][0] == nav.PAGE_SPEED
    r.press(DN_SHORT, DN_SHORT)
    assert r.navigator.page == nav.PAGE_ANCHOR               # the last page of the main loop (no Wi-Fi, no MOB)
    r.press(DN_SHORT)
    assert r.navigator.page == nav.PAGE_GPS
    r.press(DN_LONG)
    assert r.navigator.page == nav.PAGE_MAIN


def test_system_info_is_only_gathered_for_the_pages_that_need_it():
    r = Rig()
    r.step()
    assert r.system_calls == 0
    r.navigator.debug, r.navigator.page = True, nav.PAGE_SYSTEM
    r.press(UP_SHORT, DN_SHORT)                            # back to the system page via events
    assert r.system_calls >= 1 and r.draws[-1] == (nav.PAGE_SYSTEM, True)


def test_menu_opens_with_a_long_down_press_on_the_main_page_and_closes_the_same_way():
    r = Rig()
    r.press(UP_LONG)
    assert r.ui.menu is None                           # a long UP does nothing on a page
    r.press(DN_LONG)
    assert r.ui.menu is not None and r.navigator.in_menu
    assert r.oled.texts()[0] == 'Menu'                 # the menu drew itself
    r.press(DN_LONG)                                   # back at the top level: leave
    assert r.ui.menu is None and not r.navigator.in_menu


def test_menu_timeout_reverts_an_unconfirmed_edit_and_closes():
    r = Rig(menu_timeout_ms=60000)
    r.press(DN_LONG)
    for _ in range(4):
        r.press(DN_SHORT)                                  # root: GPS, Detection, Radio output, Anchor, Display
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
    r.press(DN_LONG)                                       # open the menu
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
    r.bridge.spoof = FakeDetector('HIGH')
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


def test_strong_alert_shows_a_banner_that_the_first_key_press_dismisses():
    r = Rig()
    r.step()
    assert r.ctxs[-1]['banner'] is False
    r.bridge.spoof = FakeDetector('HIGH')
    r.step(advance=10)
    assert r.ctxs[-1]['banner'] is True
    page = r.navigator.page
    r.press(UP_SHORT)                                      # dismisses the banner, does not change the page
    assert r.navigator.page == page and r.ui.alert_acked and r.ctxs[-1]['banner'] is False
    r.press(UP_SHORT)
    assert r.navigator.page != page
    r.step(advance=1000)
    assert r.ctxs[-1]['banner'] is False                   # stays dismissed while the same alert lasts
    r.bridge.spoof.state = 'OK'                            # alert over ...
    r.step(advance=1000)
    r.bridge.spoof.state = 'HIGH'                         # ... and a new one
    r.press(DN_LONG)                                       # back to Main
    r.step(advance=1000)
    assert r.ctxs[-1]['banner'] is True and not r.ui.alert_acked


def test_banner_only_swallows_keys_on_pages_that_show_it():
    r = Rig()
    r.step()
    r.press(UP_SHORT, UP_SHORT)                            # Main -> Speed -> GPS
    assert r.navigator.page == nav.PAGE_GPS
    r.bridge.spoof = FakeDetector('HIGH')
    r.step(advance=10)
    r.press(DN_SHORT)                                      # the GPS page has no banner: the key acts
    assert r.navigator.page == nav.PAGE_SPEED and not r.ui.alert_acked


def test_new_strong_alert_blinks_the_display_for_a_while_unless_dismissed():
    r = Rig()
    r.step()
    r.bridge.detector = FakeDetector('MEDIUM')
    r.step(advance=10)
    assert r.oled.inverts == [True]
    r.step(advance=ui.BLINK_HALF_MS)
    r.step(advance=ui.BLINK_HALF_MS)
    assert r.oled.inverts == [True, False, True]
    r.step(advance=ui.BLINK_MS)
    assert r.oled.inverts[-1] is False and not r.ui._inverted     # ends normal, not inverted
    r2 = Rig()
    r2.step()
    r2.bridge.detector = FakeDetector('MEDIUM')
    r2.step(advance=10)
    r2.press(UP_SHORT)
    assert r2.oled.inverts == [True, False]                # dismissing stops the blink at once


def test_suspect_or_low_neither_banner_nor_blink():
    r = Rig()
    r.bridge.spoof = FakeDetector('LOW')
    r.bridge.detector = FakeDetector('LOW')
    r.step()
    r.step(advance=100)
    assert r.ctxs[-1]['banner'] is False and r.oled.inverts == []


def test_night_mode_turns_the_screen_off_after_30_seconds_but_an_alert_keeps_it_on():
    r = Rig()
    r.cfg.set('night', 'on')
    r.step()
    r.step(advance=29000)
    assert not r.ui.screen_off
    r.step(advance=2000)
    assert r.ui.screen_off
    r.bridge.spoof = FakeDetector('HIGH')
    r.step(advance=1000)
    assert not r.ui.screen_off
    r2 = Rig(screen_off_s=30)                              # a shorter user setting still wins
    r2.cfg.set('night', 'on')
    r2.cfg.set('screen_off_s', 0)
    r2.step()
    r2.step(advance=31000)
    assert r2.ui.screen_off


def test_wifi_hold_box_appears_while_up_is_held_and_follows_the_progress():
    r = Rig()
    held = [None]
    r.ui.up_held = lambda now: held[0]
    r.step()
    assert r.ctxs[-1]['hold'] is None
    held[0] = 200
    r.step(advance=200)
    assert r.ctxs[-1]['hold'] is None                      # a short press shows nothing
    n = len(r.draws)
    held[0] = 1500
    r.step(advance=200)
    assert r.ctxs[-1]['hold'] == (50, 'Hold: Wi-Fi on') and len(r.draws) == n + 1
    held[0] = 2000
    r.step(advance=ui.HOLD_REFRESH_MS + 1)
    assert r.ctxs[-1]['hold'] == (66, 'Hold: Wi-Fi on')
    held[0] = 9000
    r.step(advance=ui.HOLD_REFRESH_MS + 1)
    assert r.ctxs[-1]['hold'] == (100, 'Release now!')
    held[0] = ui.HOLD_STALE_MS + 1                         # a lost release edge must not leave it on screen
    r.step(advance=ui.REFRESH_MS + 1)
    assert r.ctxs[-1]['hold'] is None


def test_fix_age_is_passed_while_there_is_no_fix():
    r = Rig()
    r.step()
    assert r.ctxs[-1]['fix_age_s'] is None                 # no fix ever: nothing to count from
    r.bridge.last_fix = r.clock.now - 5000
    r.bridge.parser.fix_type = 'NO'
    r.step(advance=ui.REFRESH_MS + 1)
    assert r.ctxs[-1]['fix_age_s'] == 5


def test_main_page_heartbeat_changes_redraw_at_once_and_other_pages_do_not_care():
    r = Rig()
    r.bridge.heartbeat = lambda now: state[0]
    state = ['idle']
    r.step()
    n = len(r.draws)
    r.step(advance=50)
    assert len(r.draws) == n                               # nothing changed, nothing drawn
    state[0] = 'on'
    r.step(advance=50)                                     # far sooner than REFRESH_MS
    assert len(r.draws) == n + 1 and r.ctxs[-1]['heartbeat'] == 'on'
    state[0] = 'off'
    r.step(advance=50)
    assert len(r.draws) == n + 2 and r.ctxs[-1]['heartbeat'] == 'off'
    r.press(UP_SHORT)                                      # another page: the heartbeat is not drawn there
    n = len(r.draws)
    state[0] = 'on'
    r.step(advance=50)
    assert len(r.draws) == n and r.ctxs[-1]['heartbeat'] is None


def test_main_page_gets_the_wifi_state_for_its_mark():
    r = Rig()
    r.step()
    assert r.draws[-1][0] == nav.PAGE_MAIN
    assert r.wifi_args[-1] is not None


def test_chord_switches_to_the_debug_loop_and_back():
    r = Rig()
    r.step()
    r.press(nav.CHORD)
    assert r.navigator.debug and r.navigator.page == nav.PAGE_LOG and r.ctxs[-1]['debug'] is True
    assert r.ctxs[-1]['pages'] == nav.DEBUG_PAGES
    r.press(UP_SHORT)
    assert r.navigator.page == nav.PAGE_STATS
    r.press(DN_LONG)                                       # a long DOWN leaves the debug loop
    assert not r.navigator.debug and r.navigator.page == nav.PAGE_MAIN and r.ctxs[-1]['debug'] is False
    r.press(nav.CHORD, nav.CHORD)
    assert not r.navigator.debug                           # the same chord toggles back


def test_chord_is_not_swallowed_by_the_alert_banner_and_is_ignored_in_the_menu():
    r = Rig()
    r.step()
    r.bridge.spoof = FakeDetector('HIGH')
    r.step(advance=10)
    assert r.ctxs[-1]['banner'] is True
    r.press(nav.CHORD)
    assert not r.navigator.debug and not r.ui.alert_acked or r.navigator.debug   # a chord still acts on the pages
    r2 = Rig()
    r2.step()
    r2.press(DN_LONG)
    assert r2.ui.menu is not None
    r2.press(nav.CHORD)
    assert not r2.navigator.debug and r2.ui.menu is not None


def test_a_new_alert_pulls_the_display_back_from_the_debug_loop():
    r = Rig()
    r.step()
    r.press(nav.CHORD)
    assert r.navigator.debug
    r.bridge.spoof = FakeDetector('MEDIUM')
    r.step(advance=10)
    assert not r.navigator.debug and r.navigator.page == nav.PAGE_MAIN and r.ctxs[-1]['banner'] is True


def test_the_wifi_page_follows_the_access_point():
    r = Rig()
    up = [False]
    r.ui.wifi_up = lambda: up[0]
    r.navigator.wifi = True
    r.step()
    assert nav.PAGE_WIFI not in r.navigator.pages
    up[0] = True
    r.step(advance=10)
    assert r.navigator.pages[-1] == nav.PAGE_WIFI
    r.press(DN_SHORT)                                      # Main -> last page = Wi-Fi
    assert r.navigator.page == nav.PAGE_WIFI
    up[0] = False                                          # the access point goes off while you look at it
    r.step(advance=10)
    assert r.navigator.page == nav.PAGE_MAIN


def test_chord_hold_box_shows_the_target_loop_and_progress():
    r = Rig()
    held = [None]
    r.ui.chord_held = lambda now: held[0]
    r.step()
    held[0] = 1000
    r.step(advance=200)
    assert r.ctxs[-1]['hold'] == (50, 'Hold: debug')
    r.navigator.debug, r.navigator.page = True, nav.PAGE_STATS
    held[0] = 1500
    r.step(advance=ui.HOLD_REFRESH_MS + 1)
    assert r.ctxs[-1]['hold'] == (75, 'Hold: main')


def test_an_alert_that_gets_worse_shows_the_banner_and_blink_again():
    r = Rig()
    r.step()
    r.bridge.spoof = FakeDetector('MEDIUM')
    r.step(advance=10)
    r.press(UP_SHORT)                                      # dismissed
    assert r.ui.alert_acked and r.ctxs[-1]['banner'] is False
    r.step(advance=ui.BLINK_MS + 100)
    n = len(r.oled.inverts)
    r.bridge.spoof.state = 'HIGH'
    r.step(advance=10)
    assert not r.ui.alert_acked and r.ctxs[-1]['banner'] is True and len(r.oled.inverts) > n
    r.press(UP_SHORT)
    r.bridge.spoof.state = 'MEDIUM'                        # getting better does not bring it back
    r.step(advance=10)
    assert r.ui.alert_acked
    r.bridge.spoof.state = 'HIGH'                          # but getting worse again does
    r.step(advance=10)
    assert not r.ui.alert_acked


def test_the_debug_loop_times_out_without_keys():
    r = Rig()
    r.step()
    r.press(nav.CHORD)
    assert r.navigator.debug
    r.step(advance=ui.DEBUG_TIMEOUT_MS - 1000)
    assert r.navigator.debug
    r.press(UP_SHORT)                                      # a key press restarts the timer
    r.step(advance=ui.DEBUG_TIMEOUT_MS - 1000)
    assert r.navigator.debug
    r.step(advance=2000)
    assert not r.navigator.debug and r.navigator.page == nav.PAGE_MAIN
    assert r.draws[-1][0] == nav.PAGE_MAIN


def test_the_main_page_waits_for_the_first_fix_and_passes_the_uptime():
    r = Rig()
    r.step()
    assert r.ctxs[-1]['fix_age_s'] is None and r.ctxs[-1]['uptime_s'] == 0
    r.step(advance=2100)
    assert r.ctxs[-1]['uptime_s'] == 2                     # redrawn every second while waiting
    r.press(UP_SHORT)
    assert r.ctxs[-1]['uptime_s'] is None                  # only the Main page needs it


def test_speed_trend_needs_ten_seconds_of_history_and_follows_the_change():
    r = Rig()
    r.bridge.parser.fix_type = 'GPS'
    p = r.bridge.parser

    def run(seconds, speed_at):
        for s in range(seconds):
            r.bridge.last_pos = r.clock.now                 # keep the fix fresh
            p.sog_kn = speed_at(s)
            r.step(advance=1000)
    run(3, lambda s: 5.0)
    assert r.ui.sog_trend() is None                         # not enough history yet
    run(10, lambda s: 5.0)
    assert r.ui.sog_trend() == 0
    run(12, lambda s: 5.0 + 0.2 * s)                        # accelerating about 0.2 kn per second
    assert r.ui.sog_trend() == 1
    run(12, lambda s: 7.4 - 0.2 * s)
    assert r.ui.sog_trend() == -1
    p.sog_kn = None
    r.step(advance=1000)
    assert r.ui.sog_trend() is None                         # no speed: the history is dropped


def test_alerts_page_gets_the_newest_entries_and_the_total():
    r = Rig()
    r.step()
    r.press(nav.CHORD)
    assert r.navigator.page == nav.PAGE_LOG
    assert r.ctxs[-1]['alerts'] == [] and r.ctxs[-1]['alert_total'] == 0
    for i in range(7):
        r.bridge.log_alert('SPF?', 'K{}'.format(i))
    r.step(advance=ui.REFRESH_MS + 1)
    assert [e[2] for e in r.ctxs[-1]['alerts']] == ['K6', 'K5', 'K4', 'K3', 'K2'] and r.ctxs[-1]['alert_total'] == 7


def test_alarm_level_follows_the_alert_and_any_key_press_silences_it_until_it_gets_worse():
    import alerts
    r = Rig()
    r.step()
    assert r.ui.alarm_level() == alerts.NONE
    r.bridge.spoof = FakeDetector('MEDIUM')
    r.step(advance=10)
    assert r.ui.alarm_level() == alerts.MEDIUM
    r.press(UP_SHORT)                                      # any key, on any page
    assert r.ui.alarm_level() == alerts.NONE
    r.bridge.spoof.state = 'HIGH'                          # worse: sounds again
    r.step(advance=10)
    assert r.ui.alarm_level() == alerts.HIGH
    r.press(nav.CHORD)
    assert r.ui.alarm_level() == alerts.NONE
    r.bridge.spoof.state = 'OK'
    r.step(advance=10)
    r.bridge.spoof.state = 'MEDIUM'                        # a new alert after it ended
    r.step(advance=10)
    assert r.ui.alarm_level() == alerts.MEDIUM


HOME = (59 * 600000 + 183420, 18 * 600000 + 32190)


def with_fix(r, position=HOME):
    """Give the rig's bridge a fresh fix at a position."""
    p = r.bridge.parser
    p.fix_type, p.lat_u, p.lon_u = 'GPS', position[0], position[1]
    p.fix_count += 1
    r.bridge.last_pos = r.bridge.last_fix = r.clock.now


def test_anchor_page_long_down_drops_and_long_up_lifts_the_anchor():
    import anchor
    r = Rig()
    r.bridge.anchor = anchor.AnchorWatch(radius_m=50)
    r.step()
    r.press(UP_SHORT, UP_SHORT, UP_SHORT)                  # Main -> Speed -> GPS -> Anchor
    assert r.navigator.page == nav.PAGE_ANCHOR and r.ctxs[-1]['anchor'] is r.bridge.anchor
    r.press(UP_LONG)                                       # nothing to lift yet
    assert r.ctxs[-1]['toast'] == 'No anchor set'
    r.press(DN_LONG)                                       # no fix yet
    assert not r.bridge.anchor.is_set and r.ctxs[-1]['toast'] == 'No fix yet' and r.navigator.page == nav.PAGE_ANCHOR
    with_fix(r)
    r.press(DN_LONG)
    assert r.bridge.anchor.anchor == HOME and r.ctxs[-1]['toast'] == 'Anchor dropped'
    r.step(advance=ui.TOAST_MS + 100)
    assert 'toast' not in r.ctxs[-1]
    r.press(DN_LONG)                                       # dropping again does not move it
    assert r.bridge.anchor.anchor == HOME and r.ctxs[-1]['toast'] == 'Anchor is set'
    r.press(UP_LONG)                                       # lifted, no question asked
    assert not r.bridge.anchor.is_set and r.ctxs[-1]['toast'] == 'Anchor lifted'
    r.press(WIFI)                                          # the Wi-Fi gesture does nothing here ...
    assert r.toggles == 0
    r.press(DN_SHORT, DN_SHORT, DN_SHORT)                  # ... and works on the Main page
    assert r.navigator.page == nav.PAGE_MAIN
    r.press(WIFI)
    assert r.toggles == 1





def test_anchor_alarm_banner_urgent_buzzer_snooze_and_repeat():
    import alerts
    import anchor
    r = Rig()
    r.bridge.anchor = anchor.AnchorWatch(radius_m=50)
    r.bridge.anchor.set(HOME)
    with_fix(r)
    r.step()
    for _ in range(3):
        with_fix(r, (HOME[0] + 1080, HOME[1]))             # 200 m north
        r.bridge.step(r.clock.now)                         # (the rig steps the controller only)
        r.step(advance=1000)
    assert r.bridge.anchor.alarming
    assert r.ctxs[-1]['banner'] is True and r.ctxs[-1]['banner_text'] == 'ANCHOR DRAG'
    assert r.ui.alarm_level() == alerts.URGENT
    r.press(UP_SHORT)                                      # dismisses the banner and silences the buzzer
    assert r.ui.alarm_level() == alerts.NONE and r.ctxs[-1]['banner'] is False
    r.step(advance=ui.SNOOZE_MS - 5000)
    with_fix(r, (HOME[0] + 1080, HOME[1]))
    r.bridge.step(r.clock.now)
    assert r.ui.alarm_level() == alerts.NONE
    r.step(advance=6000)
    assert r.ui.alarm_level() == alerts.URGENT and r.ctxs[-1]['banner'] is True      # still dragging: again
    r.bridge.anchor.state = 'nofix'
    r.step(advance=ui.REFRESH_MS + 1)
    assert r.ctxs[-1]['banner_text'] == 'ANCHOR NO FIX'


def test_man_overboard_gesture_marks_the_position_and_raises_the_alarm_from_any_page():
    import alerts
    r = Rig()
    r.step()
    r.press(nav.CHORD)                                     # in the debug loop
    r.press(nav.MOB)
    assert not r.bridge.mob.active and r.ctxs[-1]['toast'] == 'No position yet' and r.navigator.debug is False
    with_fix(r)
    r.press(nav.MOB)
    mob = r.bridge.mob
    assert mob.active and mob.position == HOME and r.navigator.page == nav.PAGE_MOB
    assert r.navigator.mob_active and nav.PAGE_MOB in r.navigator.pages
    assert r.ctxs[-1]['banner_text'] == 'MAN OVERBOARD' and r.ctxs[-1]['mob'] is mob
    assert r.ui.alarm_level() == alerts.URGENT and r.bridge.alert_log.entries[-1][1:] == ('MOB!', 'marked')
    r.press(DN_SHORT)                                      # the first key press acknowledges (banner, buzzer)
    assert not mob.alerting and r.ui.alarm_level() == alerts.NONE and mob.active
    r.press(nav.MOB)                                       # DN held again: no second mark, just the page
    assert r.ctxs[-1]['toast'] == 'MOB already marked' and r.bridge.alert_log.total == 1
    assert r.navigator.page == nav.PAGE_MOB
    r.press(WIFI)                                          # UP held 3 s on the MOB page lifts the mark, no question
    assert not mob.active and r.ctxs[-1]['toast'] == 'MOB lifted' and r.toggles == 0
    assert r.navigator.page == nav.PAGE_MAIN and nav.PAGE_MOB not in r.navigator.pages
    r.press(nav.MOB)                                       # and a new one can be marked
    assert mob.active and mob.alerting


def test_man_overboard_works_with_the_screen_off_and_with_the_menu_open():
    r = Rig(screen_off_s=30)
    with_fix(r)
    r.step()
    r.step(advance=31000)
    assert r.ui.screen_off
    with_fix(r)
    r.press(nav.MOB)                                       # no key press is needed to wake it first
    assert not r.ui.screen_off and r.bridge.mob.active
    r2 = Rig()
    with_fix(r2)
    r2.press(DN_LONG)
    assert r2.ui.menu is not None
    r2.press(nav.MOB)
    assert r2.ui.menu is None and r2.bridge.mob.active and r2.navigator.page == nav.PAGE_MOB


def test_hold_box_for_man_overboard_appears_after_a_normal_long_press():
    r = Rig()
    held = [None]
    r.ui.dn_held = lambda now: held[0]
    r.step()
    held[0] = 1000
    r.step(advance=200)
    assert r.ctxs[-1]['hold'] is None                      # a normal long press (menu / back) shows nothing
    held[0] = 1800
    r.step(advance=200)
    assert r.ctxs[-1]['hold'] == (60, 'Hold: MOB')
    r.bridge.mob.set((1, 1), 0)                            # with a mark in place DN held does nothing: no box
    r.step(advance=ui.HOLD_REFRESH_MS + 1)
    assert r.ctxs[-1]['hold'] is None
    r.bridge.mob.clear()
    r.step(advance=ui.HOLD_REFRESH_MS + 1)
    held[0] = 3200
    r.step(advance=ui.HOLD_REFRESH_MS + 1)
    assert r.ctxs[-1]['hold'] == (100, 'Release now!')


def test_automatic_night_mode_follows_the_sun_with_hysteresis_and_calls_back_on_changes():
    import sun
    r = Rig()
    calls = []
    r.ui.on_night = calls.append
    r.cfg.set('night', 'auto')
    p = r.bridge.parser
    p.fix_type, p.lat_u, p.lon_u = 'GPS', int(59.33 * 600000), int(18.07 * 600000)
    p.utc_days = 20625                                       # 2026-06-21

    def at(minute_of_day):
        p.utc_ms = minute_of_day * 60000
        r.clock.now += ui.DARK_CHECK_MS + 100
        r.bridge.last_pos = r.clock.now                      # the fix stays fresh
        r.step()
    at(10 * 60 + 48)                                         # solar noon: bright
    assert not r.ui.night_active() and calls == []
    p.utc_days = 20808                                       # midwinter
    at(22 * 60)                                              # the middle of the night
    assert r.ui.night_active() and calls == [True]
    assert sun.elevation_deg(59.33, 18.07, 20808, 22 * 60000 * 60) < -30
    at(10 * 60 + 48)                                         # noon again, low sun but well above the horizon
    assert not r.ui.night_active() and calls == [True, False]
    r.cfg.set('night', 'on')
    r.step(advance=10)
    assert r.ui.night_active() and calls == [True, False, True]
    r.cfg.set('night', 'off')
    r.step(advance=10)
    assert not r.ui.night_active() and calls[-1] is False


def test_automatic_night_mode_keeps_the_last_answer_without_a_fix_and_does_not_turn_the_screen_off():
    r = Rig(screen_off_s=0)
    r.cfg.set('night', 'auto')
    p = r.bridge.parser
    p.fix_type, p.lat_u, p.lon_u, p.utc_days, p.utc_ms = 'GPS', int(59.33 * 600000), int(18.07 * 600000), 20808, 0
    r.clock.now += ui.DARK_CHECK_MS + 100
    r.bridge.last_pos = r.clock.now
    r.step()
    assert r.ui.night_active()
    r.step(advance=60000)                                    # no fix any more: the last answer stays
    assert r.ui.night_active()
    r.step(advance=600000)
    assert not r.ui.screen_off                               # auto only dims; "on" also switches the screen off


def test_man_overboard_mark_records_the_gps_time_and_the_page_counts_from_it():
    r = Rig()
    r.step()
    with_fix(r)
    p = r.bridge.parser
    p.utc_days, p.utc_ms = 20625, 12 * 3600 * 1000
    r.press(nav.MOB)
    assert r.bridge.mob.marked_utc == 20625 * 86400 + 12 * 3600
    p.utc_ms += 75000
    r.step(advance=ui.REFRESH_MS + 1)
    assert r.ctxs[-1]['mob_s'] == 75


def test_hold_box_for_up_held_is_the_wifi_on_main_lift_mob_on_its_page_and_nothing_elsewhere():
    r = Rig()
    held = [None]
    r.ui.up_held = lambda now: held[0]
    r.step()
    held[0] = 500
    r.step(advance=200)
    assert r.ctxs[-1]['hold'] == (16, 'Hold: Wi-Fi on')       # the Main page
    held[0] = 3200
    r.step(advance=ui.HOLD_REFRESH_MS + 1)
    assert r.ctxs[-1]['hold'] == (100, 'Release now!')
    held[0] = None
    r.press(UP_SHORT, UP_SHORT, UP_SHORT)                  # Anchor: UP held does nothing, so no box
    held[0] = 2000
    r.step(advance=ui.REFRESH_MS + 1)
    assert r.ctxs[-1]['hold'] is None
    r.press(UP_SHORT)                                      # the Wi-Fi is not on this page either (GPS-less loop: Main)
    r.bridge.mob.set(HOME, 0)
    r.navigator.mob_active = True
    r.navigator.page = nav.PAGE_MOB
    held[0] = 1500
    r.step(advance=ui.REFRESH_MS + 1)
    assert r.ctxs[-1]['hold'] == (50, 'Lift MOB')
    r.navigator.page = nav.PAGE_SPEED
    r.step(advance=ui.REFRESH_MS + 1)
    assert r.ctxs[-1]['hold'] is None                      # Speed: nothing
    r.navigator.page = nav.PAGE_MAIN
    r.navigator.wifi = False                               # no Wi-Fi in this build: nothing to hold for
    r.step(advance=ui.REFRESH_MS + 1)
    assert r.ctxs[-1]['hold'] is None
