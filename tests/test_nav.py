import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import nav
from nav import Navigator, classify, UP_SHORT, UP_LONG, DN_SHORT, DN_LONG, WIFI


def test_classify_presses():
    assert classify('UP', 100) == UP_SHORT
    assert classify('UP', 1000) == UP_LONG and classify('UP', 2999) == UP_LONG
    assert classify('UP', 3000) == WIFI
    assert classify('DN', 100) == DN_SHORT and classify('DN', 1500) == DN_LONG
    assert classify('DN', 9000) == DN_LONG            # the Wi-Fi gesture is UP only
    assert classify('UP', 5000, wifi_ms=None) == UP_LONG


def test_main_loop_cycle_both_directions_and_wrap_with_and_without_the_wifi_page():
    n = Navigator()
    assert n.page == nav.PAGE_MAIN and n.pages == (nav.PAGE_MAIN, nav.PAGE_SPEED, nav.PAGE_GPS)
    for expected in (nav.PAGE_SPEED, nav.PAGE_GPS):
        assert n.handle(UP_SHORT) is None and n.page == expected
    n.handle(UP_SHORT)
    assert n.page == nav.PAGE_MAIN                    # wrapped
    n.handle(DN_SHORT)
    assert n.page == nav.PAGE_GPS
    n.wifi_up = True
    assert n.pages[-1] == nav.PAGE_WIFI
    n.handle(UP_SHORT)
    assert n.page == nav.PAGE_WIFI
    n = Navigator(wifi=False)
    n.wifi_up = True
    assert nav.PAGE_WIFI not in n.pages               # a build without Wi-Fi never shows the page


def test_chord_toggles_the_debug_loop_and_a_long_down_leaves_it():
    n = Navigator()
    assert n.handle(nav.CHORD) is None and n.debug and n.page == nav.PAGE_STATS
    assert n.pages == nav.DEBUG_PAGES
    for expected in nav.DEBUG_PAGES[1:]:
        n.handle(UP_SHORT)
        assert n.page == expected
    n.handle(UP_SHORT)
    assert n.page == nav.PAGE_STATS                   # wrapped inside the debug loop
    assert n.handle(DN_LONG) is None and not n.debug and n.page == nav.PAGE_MAIN     # no menu from here
    n.handle(nav.CHORD)
    n.handle(nav.CHORD)
    assert not n.debug and n.page == nav.PAGE_MAIN


def test_chord_does_nothing_in_the_menu_and_the_wifi_page_disappears_with_the_access_point():
    n = Navigator()
    assert n.handle(DN_LONG) == nav.OPEN_MENU
    assert n.handle(nav.CHORD) is None and not n.debug and n.in_menu
    n.close_menu()
    n.wifi_up = True
    n.page = nav.PAGE_WIFI
    n.wifi_up = False
    n.check()
    assert n.page == nav.PAGE_MAIN


def test_dn_long_returns_to_main():
    n = Navigator()
    n.handle(UP_SHORT)
    n.handle(UP_SHORT)
    n.handle(DN_LONG)
    assert n.page == nav.PAGE_MAIN


def test_long_down_on_the_main_page_opens_the_menu_then_events_are_forwarded():
    n = Navigator()
    assert n.page == nav.PAGE_MAIN
    assert n.handle(UP_LONG) is None and not n.in_menu         # a long UP has no function on the pages
    assert n.handle(DN_LONG) == nav.OPEN_MENU and n.in_menu
    for ev in (UP_SHORT, DN_SHORT, UP_LONG, DN_LONG):
        assert n.handle(ev) == nav.TO_MENU
    assert n.page == nav.PAGE_MAIN                    # pages untouched while in the menu
    n.close_menu()
    assert not n.in_menu and n.handle(UP_SHORT) is None and n.page != nav.PAGE_MAIN


def test_wifi_gesture_toggles_outside_the_menu_but_is_a_long_up_inside_it():
    n = Navigator()
    assert n.normalize(WIFI) == WIFI and n.handle(n.normalize(WIFI)) == nav.TOGGLE_WIFI
    n.handle(DN_LONG)                                  # open the menu (long DOWN on the Main page)
    ev = n.normalize(WIFI)
    assert ev == UP_LONG                               # a slightly long confirm is just a confirm
    assert n.handle(ev) == nav.TO_MENU and n.in_menu
    assert n.normalize(DN_SHORT) == DN_SHORT


def test_button_tracker_pairs_press_and_release_and_classifies():
    b = nav.ButtonTracker('UP')
    assert b.edge(0, 1000) is None
    assert b.edge(1, 1200) == UP_SHORT
    assert b.edge(0, 2000) is None and b.edge(1, 3100) == UP_LONG
    assert b.edge(0, 5000) is None and b.edge(1, 8100) == WIFI
    d = nav.ButtonTracker('DN')
    d.edge(0, 0)
    assert d.edge(1, 1500) == DN_LONG


def test_button_tracker_ignores_bounce():
    b = nav.ButtonTracker('UP')
    assert b.edge(0, 1000) is None
    assert b.edge(1, 1005) is None                     # bounce right after the press edge
    assert b.edge(0, 1012) is None
    assert b.edge(1, 1300) == UP_SHORT                 # the real release, exactly one event
    assert b.edge(0, 2000) is None
    assert b.edge(1, 2400) == UP_SHORT
    assert b.edge(1, 2410) is None                     # bounce after the release: no second event
    assert b.edge(0, 2415) is None                     # ... and it must not start a phantom press
    assert b.edge(0, 3000) is None                     # a later, real press still works
    assert b.edge(1, 3200) == UP_SHORT


def test_release_without_a_press_is_ignored():
    b = nav.ButtonTracker('UP')
    assert b.edge(1, 123456789) is None                # used to be classified as a WIFI hold of "uptime" length
    assert b.edge(0, 200000000) is None
    assert b.edge(1, 200000100) == UP_SHORT


def test_button_tracker_survives_tick_wrap():
    saved = nav._ticks_diff
    nav._ticks_diff = lambda a, b: ((a - b + (1 << 29)) & ((1 << 30) - 1)) - (1 << 29)
    try:
        b = nav.ButtonTracker('UP')
        b.edge(0, (1 << 30) - 100)
        assert b.edge(1, 400) == UP_SHORT              # 500 ms across the wrap
    finally:
        nav._ticks_diff = saved


def test_event_queue_fifo_full_and_empty():
    q = nav.EventQueue(4)
    assert q.get() is None and q.drain() == []
    assert q.put('a') and q.put('b') and q.put('c')
    assert not q.put('d')                              # one slot is kept free: full, newest dropped
    assert q.drain() == ['a', 'b', 'c']
    for i in range(10):                                # wraps around many times
        assert q.put(i) and q.get() == i
    assert q.drain() == []


def test_long_down_elsewhere_goes_back_to_main_and_only_then_opens_the_menu():
    n = Navigator()
    n.handle(UP_SHORT)
    n.handle(UP_SHORT)
    assert n.page != nav.PAGE_MAIN
    assert n.handle(DN_LONG) is None and n.page == nav.PAGE_MAIN and not n.in_menu
    assert n.handle(DN_LONG) == nav.OPEN_MENU and n.in_menu     # the second long press enters the menu
    n.handle(DN_LONG)                                           # in the menu it is "back"
    n.close_menu()
    assert n.handle(DN_LONG) == nav.OPEN_MENU                   # and again from the Main page


def test_button_tracker_reports_how_long_a_key_has_been_held():
    t = nav.ButtonTracker('UP', 1000, 3000)
    assert t.held_ms(500) is None
    t.edge(0, 1000)
    assert t.held_ms(1700) == 700
    assert t.edge(1, 2000) == nav.UP_LONG
    assert t.held_ms(2100) is None


def test_two_keys_together_are_a_chord_and_neither_acts_alone():
    up, dn = nav.ButtonTracker('UP'), nav.ButtonTracker('DN')
    nav.pair(up, dn)
    up.edge(0, 1000)
    dn.edge(0, 1100)                                   # the second key goes down while the first is held
    assert up.held_ms(1500) is None                    # no single-key gesture (Wi-Fi hold box) any more
    assert nav.chord_held_ms(up, dn, 1500) == 400
    assert not nav.chord_due(up, dn, 2900) and nav.chord_due(up, dn, 3100)
    assert not nav.chord_due(up, dn, 3200)             # fires once
    assert nav.chord_held_ms(up, dn, 3300) is None
    assert up.edge(1, 4000) is None and dn.edge(1, 4100) is None      # no UP_LONG / WIFI / DN_LONG afterwards
    up.edge(0, 6000)                                   # and the keys work again on their own
    assert up.edge(1, 6200) == UP_SHORT


def test_a_short_overlap_of_both_keys_does_nothing_at_all():
    up, dn = nav.ButtonTracker('UP'), nav.ButtonTracker('DN')
    nav.pair(up, dn)
    up.edge(0, 1000)
    dn.edge(0, 1050)
    assert up.edge(1, 1300) is None and dn.edge(1, 1400) is None
    assert not nav.chord_due(up, dn, 5000)             # nothing is held any more
    dn.edge(0, 7000)
    assert dn.edge(1, 8200) == DN_LONG


def test_one_key_alone_is_unaffected_by_pairing_and_never_a_chord():
    up, dn = nav.ButtonTracker('UP'), nav.ButtonTracker('DN')
    nav.pair(up, dn)
    up.edge(0, 1000)
    assert up.held_ms(1700) == 700 and nav.chord_held_ms(up, dn, 5000) is None
    assert not nav.chord_due(up, dn, 9000)
    assert up.edge(1, 4100) == WIFI
