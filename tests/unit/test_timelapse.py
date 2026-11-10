"""Timelapse: wait splitting, sample shape, frame scheduling, step/interpolation rules, layout. No engine, no ffmpeg."""

import itertools
from datetime import date

import pytest

from railhead.timelapse import (
    GRID_DAYS,
    GRID_TICKS,
    TICKS_PER_DAY,
    Box,
    at_or_before,
    date_check,
    day_chunks,
    display_date,
    frame_of,
    layout,
    make_sample,
    money_at,
    schedule,
    split_log,
    spread,
    tracks,
)


def _log():
    return [
        {"kind": "header", "ticks": 100, "checksum": {"h": 1}},
        {"kind": "act", "seq": 1, "ticks": 100, "m": "vehicle_buy", "a": {"engine": 5}, "ok": True},
        {"kind": "wait", "seq": 2, "ticks": 100, "end_ticks": 100 + 3 * GRID_TICKS + 7, "a": {"days": 90},
         "checksum": {"h": 42}, "snap": {"date": "1960-05-01"}},
        {"kind": "meta", "seq": 3, "ticks": 100 + 3 * GRID_TICKS + 7, "m": "submit", "a": {}},
        {"kind": "wait", "seq": 4, "ticks": 100 + 3 * GRID_TICKS + 7, "end_ticks": 100 + 3 * GRID_TICKS + 50,
         "a": {"ticks": 43}, "checksum": {"h": 43}},
    ]


def test_split_log_keeps_ticks_contiguous_and_the_checksum_on_the_last_piece_only():
    recs = _log()
    out = split_log(recs)
    assert [r for r in out if r["kind"] != "wait"] == [r for r in recs if r["kind"] != "wait"]  # untouched
    waits = [r for r in out if r["kind"] == "wait"]
    t = 100
    for w in waits:  # what railhead.replay requires: each record starts where the world stands
        assert w["ticks"] == t and w["end_ticks"] > w["ticks"]
        t = w["end_ticks"]
    assert t == recs[-1]["end_ticks"]
    first = [w for w in waits if w.get("seq") == 2 or w.get("split_of") == 2]
    assert [w.get("checksum") for w in first] == [None] * (len(first) - 1) + [{"h": 42}]
    assert first[-1]["seq"] == 2 and first[-1]["snap"] == {"date": "1960-05-01"}
    assert first[0]["end_ticks"] == 101  # one tick after the act batch: a new route shows at once
    assert all(w["end_ticks"] % GRID_TICKS == 0 for w in first[1:-1])  # absolute grid, shared by all players
    second = [w for w in waits if w.get("seq") == 4 or w.get("split_of") == 4]
    assert len(second) == 1 and second[0] == recs[4]  # no act before it, no grid line inside: unchanged


def test_grid_is_shorter_than_any_month():
    assert GRID_DAYS <= 28 and GRID_TICKS == GRID_DAYS * TICKS_PER_DAY


def test_day_chunks_land_exactly_on_the_target():
    assert day_chunks(10, 10) == []
    assert day_chunks(0, 365) == [28] * 13 + [1]
    assert sum(day_chunks(5, 100)) == 95 and max(day_chunks(5, 100)) <= GRID_DAYS


def test_sample_record_shape():
    s = make_sample(ticks=5000, ymd="1961-03-04", phase="play", money_=1200, loan=500,
                    stations=[{"id": 3, "x": 10, "y": 20, "types": ["bus", "airport"]},
                              {"id": 4, "x": 30, "y": 40, "types": ["airport"]}],
                    orders={7: ("air", [{"kind": "station", "station": 3}, {"kind": "station", "station": 4}]),
                            8: ("air", [{"kind": "station", "station": 4}, {"kind": "station", "station": 3}]),
                            9: ("road", [])},
                    vehicles=[{"id": 7, "vtype": "air"}, {"id": 8, "vtype": "air"}, {"id": 9, "vtype": "road"}])
    assert set(s) == {"t", "date", "phase", "cash", "loan", "net_worth", "stations", "routes", "vehicles"}
    assert s["net_worth"] == 700
    assert s["stations"] == [[3, 10, 20, ["airport", "bus"]], [4, 30, 40, ["airport"]]]
    assert s["routes"] == [["air", 3, 4, 2]]
    assert s["vehicles"] == {"road": 1, "rail": 0, "air": 2, "water": 0}


def test_schedule_holds_then_runs_uniformly():
    sched = schedule(100, 1000, 5000, hold_start=10, hold_end=20)
    assert len(sched) == 100
    assert sched[:10] == [1000.0] * 10 and sched[-20:] == [5000.0] * 20
    assert sched[10] == 1000 and sched[79] == 5000
    assert all(b >= a for a, b in itertools.pairwise(sched))
    with pytest.raises(ValueError):
        schedule(10, 0, 1, hold_start=5, hold_end=5)


def test_frame_of_maps_ticks_and_dates_to_frames():
    sched = schedule(1800, 2294, 2294 + 4015 * TICKS_PER_DAY, hold_start=30, hold_end=120)
    assert frame_of(2294, sched) == 0
    assert frame_of(10 ** 9, sched) == 1799
    t_1966 = 2294 + (date(1966, 1, 1) - date(1960, 2, 1)).days * TICKS_PER_DAY
    f = frame_of(t_1966, sched)
    assert sched[f] >= t_1966 > sched[f - 1]
    step_days = (sched[f] - sched[f - 1]) / TICKS_PER_DAY  # ~2.4 game days a frame
    shown = display_date(sched[f], 2294, "1960-02-01")
    assert 0 <= (shown - date(1966, 1, 1)).days <= step_days


def test_display_date_counts_74_ticks_a_day():
    assert display_date(2294, 2294, "1960-02-01") == date(1960, 2, 1)
    assert display_date(2294 + 73, 2294, "1960-02-01") == date(1960, 2, 1)
    assert display_date(2294 + 29 * 74, 2294, "1960-02-01") == date(1960, 3, 1)  # 1960 is a leap year


def test_maps_never_show_the_future():
    ts = [100, 200, 300]
    assert at_or_before(ts, 50) == 0
    assert at_or_before(ts, 199.9) == 0  # a route first seen at 200 is not drawn before 200
    assert at_or_before(ts, 200) == 1
    assert at_or_before(ts, 10 ** 6) == 2


def test_money_is_linear_between_samples_and_flat_outside():
    ts, v = [100, 200, 400], [0, 1000, -1000]
    assert money_at(ts, v, 0) == 0
    assert money_at(ts, v, 150) == 500
    assert money_at(ts, v, 300) == 0
    assert money_at(ts, v, 400) == -1000 and money_at(ts, v, 999) == -1000


def test_tracks_order_by_final_net_worth_and_find_the_last_action():
    def doc(p, nws, phases):
        return {"player": p, "samples": [{"t": i * 10, "net_worth": n, "phase": ph}
                                         for i, (n, ph) in enumerate(zip(nws, phases))]}
    a = doc("a", [0, 5, 6], ["play", "idle", "runoff"]) | {"last_act_ticks": -3}
    trs = tracks([a, doc("b", [0, 9, 99], ["play", "play", "runoff"])])
    assert [t.player for t in trs] == ["b", "a"]
    assert trs[1].play_end == 0 and trs[0].play_end == 10 and trs[0].final == 99
    assert trs[1].last_act == -3  # the last act, not the last wait end, dates "last action"
    assert trs[0].last_act == trs[0].play_end  # older sample files: fall back to the last play sample


def test_date_check_compares_engine_dates_with_tick_dates():
    start = {"start_ticks": 0, "start_ymd": "1960-01-01"}
    docs = [{"samples": [{"t": 74 * 31, "date": "1960-02-01"}, {"t": 74 * 60, "date": "1960-03-03"}]}]
    assert date_check(docs, start) == 2


def test_spread_keeps_order_and_gap():
    ys = [1.0, 1.1, 1.15, 5.0]
    out = spread(ys, 0.5)
    assert out[0] == 1.0 and out[-1] == 5.0
    assert all(b - a >= 0.5 - 1e-9 for a, b in itertools.pairwise(out))
    assert all(o >= y for o, y in zip(out, ys))


def test_layout_fits_1080p_without_overlaps():
    L = layout(11)
    assert len(L["maps"]) == 11 and L["legend"] is not None
    boxes = [L["header"], *L["maps"], *L["labels"], L["bars"], L["lines"]]
    for b in boxes:
        assert b.x >= 16 - 1e-9 and b.y >= 0 and b.x + b.w <= 1920 - 16 + 1e-9 and b.y + b.h <= 1080 - 16 + 1e-9
    for i, a in enumerate(boxes):
        for b in boxes[i + 1:]:
            assert not a.overlaps(b), (a, b)
    assert all(abs(m.w - m.h) < 1e-9 and m.w >= 260 for m in L["maps"])  # square maps, big enough to read


def test_box_fraction_flips_y():
    assert Box(0, 0, 1920, 540).frac() == (0.0, 0.5, 1.0, 0.5)
