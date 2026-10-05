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


def test_page_cycle_both_directions_and_wrap():
    n = Navigator()
    assert n.page == nav.PAGE_MAIN
    for expected in nav.PAGES[1:]:
        assert n.handle(UP_SHORT) is None and n.page == expected
    n.handle(UP_SHORT)
    assert n.page == nav.PAGE_MAIN                    # wrapped
    n.handle(DN_SHORT)
    assert n.page == nav.PAGES[-1]


def test_dn_long_returns_to_main():
    n = Navigator()
    n.handle(UP_SHORT)
    n.handle(UP_SHORT)
    n.handle(DN_LONG)
    assert n.page == nav.PAGE_MAIN


def test_up_long_opens_menu_then_events_are_forwarded():
    n = Navigator()
    assert n.handle(UP_LONG) == nav.OPEN_MENU and n.in_menu
    for ev in (UP_SHORT, DN_SHORT, UP_LONG, DN_LONG):
        assert n.handle(ev) == nav.TO_MENU
    assert n.page == nav.PAGE_MAIN                    # pages untouched while in the menu
    n.close_menu()
    assert not n.in_menu and n.handle(UP_SHORT) is None and n.page != nav.PAGE_MAIN


def test_wifi_event_works_everywhere():
    n = Navigator()
    assert n.handle(WIFI) == nav.TOGGLE_WIFI
    n.handle(UP_LONG)
    assert n.handle(WIFI) == nav.TOGGLE_WIFI and n.in_menu


def test_custom_page_list_and_unknown_page():
    pages = (nav.PAGE_MAIN, nav.PAGE_STATS)
    n = Navigator(pages)
    n.handle(UP_SHORT)
    assert n.page == nav.PAGE_STATS
    n.page = nav.PAGE_WIFI                            # not in this build's list
    n.handle(UP_SHORT)
    assert n.page == nav.PAGE_MAIN
