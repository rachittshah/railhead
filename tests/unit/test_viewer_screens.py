"""The viewer opens only on the LG monitor: bounds come from the real display list, never from constants."""

import pytest

from railhead.viewer import inside, lg_bounds

MAIN = {"name": "Built-in Retina Display", "x": 0, "y": 0, "w": 1512, "h": 982}
LG = {"name": "LG ULTRAGEAR", "x": -1080, "y": -480, "w": 1080, "h": 1920}  # portrait, left of the main display


def test_lower_half_of_the_lg_in_top_left_coordinates():
    # Cocoa frames grow up from the bottom of the main display; System Events places windows from the top.
    # LG top edge: 982 - (-480 + 1920) = -458, so the lower half starts at -458 + 960 = 502.
    assert lg_bounds([MAIN, LG]) == (-1080, 502, 1080, 960)
    assert lg_bounds([MAIN, LG], half="full") == (-1080, -458, 1080, 1920)


def test_refuses_when_no_lg_monitor_is_connected():
    with pytest.raises(RuntimeError, match="LG monitor not connected"):
        lg_bounds([MAIN])


def test_inside_checks_a_window_against_the_lg_area():
    area = lg_bounds([MAIN, LG], half="full")
    assert inside((-1080, 502, 1080, 960), area)
    assert not inside((0, 0, 800, 600), area)  # on the built-in display
