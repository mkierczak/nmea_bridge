"""User-interface controller: key events, menu, screen-off timer and redrawing.

Free of hardware imports (the oled and everything else is injected) so it can be tested on a desktop.
Uptime and idle time are accumulated from the (small) differences between consecutive steps, so they
stay correct after MicroPython's millisecond counter wraps, which `ticks_diff` over days cannot do.
"""
import gc

import alerts
import nav

try:
    from utime import ticks_diff
except ImportError:
    def ticks_diff(a, b):
        return a - b

MENU_TIMEOUT_MS = 60 * 1000
REFRESH_MS = 500
NIGHT_OFF_S = 30              # night mode: the screen goes off after this much idle time (an alert keeps it on)
BLINK_MS = 3000               # a new strong alert blinks the display (inverted) for this long ...
BLINK_HALF_MS = 400           # ... in phases of this length
HOLD_SHOW_MS = 400            # the Wi-Fi gesture box appears once UP has been held this long
HOLD_STALE_MS = 10 * 1000     # a key 'held' longer than this is a lost release edge: ignore it
HOLD_REFRESH_MS = 150
TREND_SPAN_MS = 10 * 1000      # the speed trend compares the speed now with the speed this long ago
TREND_DELTA_KN = 0.5          # ... and calls it rising or falling beyond this
DEBUG_TIMEOUT_MS = 2 * 60 * 1000   # no key for this long in the debug loop: back to the Main page


class UiController(object):

    def __init__(self, cfg, oled, font, draw, navigator, events, bridge, menu_factory,
                 wifi_info, system_info, wifi_toggle, clock,
                 menu_timeout_ms=MENU_TIMEOUT_MS, refresh_ms=REFRESH_MS, up_held=None, wifi_ms=3000,
                 chord_held=None, chord_ms=nav.CHORD_MS, wifi_up=None):
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
        self.up_held = up_held                # () -> ms the UP key has been held (None if not), or no gesture
        self.wifi_ms = wifi_ms
        self.chord_held = chord_held          # (now) -> ms both keys have been held together, or None
        self.chord_ms = chord_ms
        self.wifi_up = wifi_up                # () -> True while the access point is on (or failed): its page is shown
        self.alert_acked = False              # a key press dismissed the banner of the current strong alert
        self.alarm_silenced = False           # any key press silences the buzzer until the alert gets worse
        self._rank = 0                        # 0 none, 1 MEDIUM, 2 HIGH: the worst alert of the detectors
        self._alert_ms = 0                    # uptime when it began
        self._inverted = False
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
        self._last_heartbeat = None
        self._sog_hist = []                   # [(uptime ms, knots)], one a second, for the speed trend
        self._hist_ms = 0

    def step(self, now):
        self._advance(now)
        if self.wifi_up is not None:
            self.navigator.wifi_up = self.wifi_up()
        self.navigator.check()
        self._note_speed()
        if self.navigator.debug and self.idle_ms > DEBUG_TIMEOUT_MS:
            self.navigator.leave_debug()      # nobody is diagnosing: do not leave the details on the screen
            self.force_draw = True
        for ev in self.events.drain():
            self._handle(ev)
        if self.menu is not None and self.idle_ms > self.menu_timeout_ms:
            self._close_menu(cancel=True)     # an unconfirmed edit is reverted, not left half-applied
            self.force_draw = True
        # the screen-off timer; an active jamming/spoofing alert always wakes the display and keeps it on
        alert = self.bridge.alert()
        self._track_alert()
        off_s = self.cfg.get('screen_off_s')
        if self.cfg.get('night') and (off_s == 0 or off_s > NIGHT_OFF_S):
            off_s = NIGHT_OFF_S
        if alert:
            self.screen_idle_ms = 0           # an alert counts as activity: the screen stays on after it clears
            if self.screen_off:
                self._wake()
        elif off_s and not self.screen_off and self.screen_idle_ms > off_s * 1000:
            self.screen_off = True
            self.oled.sleep(True)
        self._draw(now)

    # --- internals ------------------------------------------------------------------------
    def _note_speed(self):
        """Once a second remember the speed, about 10 s of it, for the trend mark on the Speed page."""
        if self.uptime_ms - self._hist_ms < 1000:
            return
        self._hist_ms = self.uptime_ms
        sog = self.bridge.parser.sog_kn
        hist = self._sog_hist
        if sog is None or self.bridge.no_fix(self._last_tick):
            del hist[:]
            return
        hist.append((self.uptime_ms, sog))
        while len(hist) > 1 and self.uptime_ms - hist[1][0] >= TREND_SPAN_MS:
            del hist[0]

    def sog_trend(self):
        """1 rising, -1 falling, 0 steady over the last ~10 s; None while there is not enough speed history."""
        hist = self._sog_hist
        if len(hist) < 2 or self.uptime_ms - hist[0][0] < TREND_SPAN_MS - 2000:
            return None
        delta = hist[-1][1] - hist[0][1]
        return 1 if delta >= TREND_DELTA_KN else -1 if delta <= -TREND_DELTA_KN else 0

    @property
    def _strong(self):
        return self._rank > 0

    def _alert_rank(self):
        b = self.bridge
        rank = 0
        for detector in (b.spoof, b.detector):
            if detector is not None:
                state = detector.state
                rank = max(rank, 2 if state == 'HIGH' else 1 if state == 'MEDIUM' else 0)
        return rank

    def _track_alert(self):
        """Banner and blink bookkeeping: a new alert, or one that gets worse (MEDIUM to HIGH), shows the banner
        again, starts the blink and pulls the display back from the debug loop."""
        rank = self._alert_rank()
        if rank > self._rank:
            self.alert_acked = False
            self.alarm_silenced = False
            self._alert_ms = self.uptime_ms
            if self.navigator.debug and self.menu is None:
                self.navigator.leave_debug()          # an alert must be seen: back to the Main page
            self.force_draw = True
        elif rank == 0 and self._rank:
            self.alert_acked = False
            self.alarm_silenced = False
            self.force_draw = True
        self._rank = rank
        strong = rank > 0
        blink = (strong and not self.alert_acked and not self.screen_off
                 and self.uptime_ms - self._alert_ms < BLINK_MS
                 and (self.uptime_ms - self._alert_ms) // BLINK_HALF_MS % 2 == 0)
        if blink != self._inverted:
            invert = getattr(self.oled, 'invert', None)
            if invert is not None:
                invert(blink)
            self._inverted = blink

    def alarm_level(self):
        """What the buzzer should be doing: alerts.NONE, MEDIUM or HIGH (silenced by any key press)."""
        if self.alarm_silenced or self._rank == 0:
            return alerts.NONE
        return alerts.HIGH if self._rank == 2 else alerts.MEDIUM

    def _banner_visible(self):
        return (self._strong and not self.alert_acked and self.menu is None and not self.navigator.debug
                and self.navigator.page in (nav.PAGE_MAIN, nav.PAGE_SPEED))

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
        self.alarm_silenced = True            # whatever the key was for, it also silences the buzzer
        self.idle_ms = 0
        self.screen_idle_ms = 0
        self.force_draw = True
        if self.screen_off:
            self._wake()                      # the press that wakes the display does nothing else
            return
        if self._banner_visible() and ev != nav.CHORD:
            self.alert_acked = True               # the first key press dismisses the banner (and the blink)
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

    def _hold(self, now):
        """(percent, label) while a two-key or UP gesture is being held (the box that shows its progress)."""
        if self.menu is not None:
            return None
        if self.chord_held is not None:
            held = self.chord_held(now)
            if held is not None and HOLD_SHOW_MS <= held <= HOLD_STALE_MS:
                return (min(100, held * 100 // self.chord_ms),
                        'Hold: main' if self.navigator.debug else 'Hold: debug')
        if self.up_held is None:
            return None
        held = self.up_held(now)
        if held is None or held < HOLD_SHOW_MS or held > HOLD_STALE_MS:
            return None
        state = self.wifi_info()[0]
        percent = min(100, held * 100 // self.wifi_ms)
        if percent >= 100:
            return percent, 'Release now!'
        return percent, 'Hold: Wi-Fi ' + ('off' if state.startswith('ON') else 'on')

    def _draw(self, now):
        if self.screen_off:
            return
        hold = self._hold(now)
        page = self.navigator.page
        # the heartbeat of the Main page pulses faster than the refresh period: its changes redraw at once
        heartbeat = self.bridge.heartbeat(now) if page == nav.PAGE_MAIN and self.menu is None else None
        pulse = heartbeat != self._last_heartbeat
        if not (self.force_draw or hold or pulse or ticks_diff(now, self._last_draw) > self.refresh_ms):
            return
        if hold and not (self.force_draw or pulse) and ticks_diff(now, self._last_draw) < HOLD_REFRESH_MS:
            return
        self._last_draw = now
        self._last_heartbeat = heartbeat
        redraw_all = self.force_draw or bool(hold)
        self.force_draw = False
        if self.menu is not None:
            self.menu.draw(self.oled)
            self.oled.show()
            return
        bridge = self.bridge
        parser = bridge.parser
        no_fix = bridge.no_fix(now)
        info = self.system_info() if page in (nav.PAGE_SYSTEM, nav.PAGE_SPOOF) else None
        wifi = self.wifi_info() if page in (nav.PAGE_WIFI, nav.PAGE_MAIN) else None   # the Main page shows a mark
        age_ms = bridge.fix_age_ms(now) if no_fix else None
        ctx = {'pages': self.navigator.pages, 'banner': self._banner_visible(), 'hold': hold,
               'debug': self.navigator.debug,
               'heartbeat': heartbeat, 'fix_age_s': None if age_ms is None else age_ms // 1000,
               'uptime_s': self.uptime_ms // 1000 if page == nav.PAGE_MAIN and no_fix else None,
               'sog_trend': self.sog_trend() if page == nav.PAGE_SPEED else None}
        if page == nav.PAGE_LOG:
            ctx['alerts'] = bridge.alert_log.newest(5)
            ctx['alert_total'] = bridge.alert_log.total
        sig = (page, no_fix, bridge.queue.dropped, tuple(bridge.stats.values()),
               parser.display_signature(),
               bridge.detector.signature() if bridge.detector else None,
               bridge.spoof.signature() if bridge.spoof else None,
               wifi,
               tuple(v for k, v in info.items() if k != 'now_ms') if info else None,
               parser.type_signature() if page in (nav.PAGE_STATS, nav.PAGE_DEBUG) else None,
               ctx['banner'], ctx['fix_age_s'], ctx['uptime_s'], bool(hold), heartbeat,
               (parser.sog_kn, parser.cog_deg, ctx['sog_trend']) if page == nav.PAGE_SPEED else None,
               (bridge.alert_log.total, ctx['alert_total']) if page == nav.PAGE_LOG else None)
        if redraw_all or sig != self._last_sig:
            self.draw(self.oled, self.font, page, parser, bridge.stats, bridge.queue.dropped, no_fix,
                      bridge.detector, bridge.spoof, wifi, info, ctx)
            self.oled.show()
            self._last_sig = sig
