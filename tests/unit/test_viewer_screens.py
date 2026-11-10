"""Where the viewer window goes: the LG monitor when it is connected (the owner's setup), else the main
display, or the bounds that --bounds gives. Bounds come from the real display list, never from constants.
osascript is mocked: no test opens a window or starts an engine."""

import subprocess

import pytest

from railhead import viewer
from railhead.viewer import inside, parse_bounds, pick_display, plan_window

MAIN = {"name": "Built-in Retina Display", "x": 0, "y": 0, "w": 1512, "h": 982}
LG = {"name": "LG ULTRAGEAR", "x": -1080, "y": -480, "w": 1080, "h": 1920}  # portrait, left of the main display
DELL = {"name": "DELL U2720Q", "x": 1512, "y": 0, "w": 2560, "h": 1440}


def test_lower_half_of_the_lg_in_top_left_coordinates():
    # Cocoa frames grow up from the bottom of the main display; System Events places windows from the top.
    # LG top edge: 982 - (-480 + 1920) = -458, so the lower half starts at -458 + 960 = 502.
    target, area = plan_window([MAIN, LG])
    assert target == (-1080, 502, 1080, 960)
    assert area == (-1080, -458, 1080, 1920)


def test_the_lg_wins_over_the_main_display_and_other_monitors():
    assert pick_display([MAIN, DELL, LG]) is LG
    assert pick_display([MAIN, {**LG, "name": "lg hdr 4k"}])["name"] == "lg hdr 4k"


def test_without_an_lg_the_main_display_is_used():
    assert pick_display([MAIN, DELL]) is MAIN
    target, area = plan_window([MAIN, DELL])
    assert area == (0, 0, 1512, 982)
    assert target == (0, 491, 1512, 491)  # lower half of the main display


def test_no_display_is_an_error():
    with pytest.raises(RuntimeError, match="no display"):
        pick_display([])


def test_explicit_bounds_are_used_on_the_display_that_holds_them():
    target, area = plan_window([MAIN, DELL], bounds=(1600, 100, 1200, 800))
    assert target == (1600, 100, 1200, 800)
    assert area == (1512, -458, 2560, 1440)  # bottom-aligned with the main display: top = 982 - 1440


def test_explicit_bounds_off_every_display_are_refused():
    with pytest.raises(ValueError, match="not inside one display"):
        plan_window([MAIN], bounds=(5000, 0, 800, 600))


@pytest.mark.parametrize("text, want", [("0,0,800,600", (0, 0, 800, 600)), ("-1080, 502, 1080, 960", (-1080, 502, 1080, 960))])
def test_parse_bounds(text, want):
    assert parse_bounds(text) == want


@pytest.mark.parametrize("text", ["0,0,800", "a,b,c,d", "0,0,0,600", "0,0,800,-1"])
def test_parse_bounds_refuses_bad_values(text):
    with pytest.raises(ValueError, match="x,y,w,h"):
        parse_bounds(text)


def test_inside_checks_a_window_against_an_area():
    _, area = plan_window([MAIN, LG])
    assert inside((-1080, 502, 1080, 960), area)
    assert not inside((0, 0, 800, 600), area)  # on the built-in display


def test_screens_calls_osascript_with_a_timeout(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append((cmd, kw))
        return subprocess.CompletedProcess(cmd, 0, stdout='[{"name": "LG", "x": 0, "y": 0, "w": 10, "h": 10}]')

    monkeypatch.setattr(viewer.subprocess, "run", run)
    assert viewer.screens()[0]["name"] == "LG"
    assert calls[0][0][0] == "osascript" and calls[0][1]["timeout"] > 0


def test_a_hung_osascript_becomes_a_runtime_error(monkeypatch):
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(viewer.subprocess, "run", run)
    monkeypatch.setattr(viewer.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="osascript"):
        viewer.screens()
    assert viewer._window_bounds() is None
    assert viewer._place_window((0, 0, 10, 10), tries=2) is False


def test_cli_parses_bounds_days_speed_and_log(monkeypatch):
    got = {}
    monkeypatch.setattr(viewer, "view", lambda task, **kw: got.update(task=task, **kw))
    assert viewer.main(["arena-dev-0005", "--days", "30", "--bounds", "10,20,800,600", "--speed", "200"]) == 0
    assert got == {"task": "arena-dev-0005", "days": 30, "speed": 200, "log": None, "bounds": (10, 20, 800, 600)}


def test_cli_attach_passes_bounds(monkeypatch):
    got = {}
    monkeypatch.setattr(viewer, "attach", lambda state, **kw: got.update(state=str(state), **kw))
    assert viewer.main(["--attach", "/tmp/st", "--bounds", "0,0,800,600"]) == 0
    assert got == {"state": "/tmp/st", "bounds": (0, 0, 800, 600)}


def test_cli_reports_bad_bounds_without_starting_anything(monkeypatch, capsys):
    monkeypatch.setattr(viewer, "view", lambda *a, **k: pytest.fail("view must not run"))
    assert viewer.main(["arena-dev-0005", "--bounds", "0,0,800"]) == 1
    assert "x,y,w,h" in capsys.readouterr().err
