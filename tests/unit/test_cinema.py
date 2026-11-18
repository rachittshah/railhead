"""Waybill cinema: sampling grid, events from the exchange state, the contract layer, timing, captions,
camera, excerpts, layout and one rendered frame from tiny synthetic samples. No engine, no ffmpeg."""

import itertools
import json

import pytest

from railhead.cinema import story
from railhead.cinema.sample import compare, exchange_counts, letter
from railhead.multi.replay import grid_pieces

D = story.TICKS_PER_DAY


# ============================================================================================ fixtures
def _state():
    """C1: licence from B (grantor "1") to A (grantee "0"), fee 300 in three instalments: O1 paid on time,
    O2 paid late, O3 broken; then an untagged payment from A to B (a late payment with a memo). T1: a
    tender won by B, broken. Two messages."""
    c1 = {"id": "C1", "kind": "licence", "status": "settled", "outcome": "ended", "offerer": "0", "to": "1",
          "created_tick": 100, "signed_tick": 110, "tender_id": None,
          "terms": {"grantor": "1", "grantee": "0", "rect": [10, 10, 20, 20], "fee": 300, "window_start": 110,
                    "deadline": 5000},
          "terms_history": [{"from_tick": 110, "terms": {"grantor": "1", "grantee": "0", "rect": [10, 10, 30, 20],
                                                          "fee": 300, "window_start": 110, "deadline": 5000}}],
          "events": [{"tick": 100, "event": "offer"}, {"tick": 110, "event": "accept"},
                     {"tick": 4000, "event": "settle"}]}
    t1 = {"id": "T1", "kind": "supply", "status": "settled", "outcome": "broken", "offerer": "exchange", "to": "1",
          "created_tick": 200, "signed_tick": 200, "tender_id": "T1",
          "terms": {"seller": "1", "buyer": "exchange", "price": 900, "window_start": 200, "deadline": 3000},
          "terms_history": [], "events": [{"tick": 200, "event": "award"}, {"tick": 3000, "event": "settle"}]}
    obl = {
        "O1": {"id": "O1", "contract_id": "C1", "payer": "0", "payee": "1", "amount": 100, "due": 1000,
               "settle_tick": 1500, "paid": 100, "payments": [[1000, 100]], "collected": 0, "status": "kept",
               "settled_tick": 1500},
        "O2": {"id": "O2", "contract_id": "C1", "payer": "0", "payee": "1", "amount": 100, "due": 2000,
               "settle_tick": 2500, "paid": 100, "payments": [[2600, 100]], "collected": 0, "status": "kept",
               "settled_tick": 2700},
        "O3": {"id": "O3", "contract_id": "C1", "payer": "0", "payee": "1", "amount": 100, "due": 3000,
               "settle_tick": 3500, "paid": 0, "payments": [], "collected": 0, "status": "broken",
               "settled_tick": 3500},
        "O4": {"id": "O4", "contract_id": "C9", "payer": "1", "payee": "0", "amount": 50, "due": 900,
               "settle_tick": 900, "paid": 0, "payments": [], "collected": 50, "status": "kept",
               "settled_tick": 900},
        "O5": {"id": "O5", "contract_id": "C9", "payer": "1", "payee": "0", "amount": 50, "due": 950,
               "settle_tick": 950, "paid": 0, "payments": [], "collected": 0, "status": "replaced",
               "settled_tick": None},
    }
    hist = [{"tick": 1000, "src": "0", "dst": "1", "amount": 100, "reason": "pay", "ref": "C1"},
            {"tick": 2600, "src": "0", "dst": "1", "amount": 100, "reason": "pay", "ref": "C1"},
            {"tick": 3700, "src": "0", "dst": "1", "amount": 100, "reason": "pay", "ref": "O3 late, sorry"},
            {"tick": 50, "src": "1", "dst": "0", "amount": 7, "reason": "pay", "ref": ""}]
    msgs = [{"seq": 3, "tick": 105, "from": "1", "to": "0", "text": "Company B: happy to license that corner to you."},
            {"seq": 9, "tick": 3600, "from": "0", "to": "1", "text": "sorry, I missed the instalment due at tick 3000."}]
    return {"contracts": {"C1": c1, "T1": t1}, "obligations": obl, "messages": msgs,
            "ledger": {"history": hist}, "tenders": {"T1": {"industry": 5}}, "n_records": 40}


# ============================================================================================ sampling
def test_grid_pieces_stop_on_an_absolute_grid_and_sum_to_the_step():
    for now, end, grid, origin in [(100, 1000, 148, 30), (30, 31, 148, 30), (178, 474, 148, 30), (0, 10, 0, 0)]:
        pieces = grid_pieces(now, end, grid, origin)
        assert sum(pieces) == end - now and all(p > 0 for p in pieces)
        stops = list(itertools.accumulate([now, *pieces]))[1:-1]
        assert all((s - origin) % grid == 0 for s in stops)  # the same stops in every log of one start
    assert grid_pieces(178, 474, 148, 30) == [148, 148]  # stops on grid points never make empty pieces
    assert grid_pieces(5, 5, 148, 0) == []


def test_compare_names_every_number_that_differs_from_result_json():
    result = {"verify": {"acts": 3, "steps": 2, "end_ticks": 900},
              "companies": [{"id": 0, "net_worth": 10, "net_worth_horizon": 5, "ledger_balance": -1, "score": 9}],
              "contracts": {"signed": 1, "obligations_broken": 0}}
    got = {"verify": {"acts": 3, "steps": 2, "end_ticks": 900},
           "companies": [{"id": 0, "net_worth": 10, "net_worth_horizon": 5, "ledger_balance": -1, "score": 9}],
           "contracts": {"signed": 1, "obligations_broken": 0}}
    assert compare(result, got) == []
    got["companies"][0]["score"] = 8
    got["verify"]["steps"] = 3
    got["contracts"]["signed"] = 2
    out = compare(result, got)
    assert len(out) == 3 and any("score" in m for m in out) and any("steps" in m for m in out)
    assert any("contracts.signed" in m for m in out)


def test_exchange_counts_uses_the_arena_statistics_and_counts_licences_in_force():
    st = _state()
    comps = [{"id": 0, "slot": 0}, {"id": 1, "slot": 1}]
    early, late = exchange_counts(st, comps, 200), exchange_counts(st, comps, 5000)
    assert early["signed"] == 1 and early["live_licences"] == 0  # settled: no longer in force
    assert early["messages"] == 2 and early["records"] == 40 and early["obligations_broken"] == 1
    assert late["live_licences"] == 0
    st["contracts"]["C1"]["status"] = "active"
    assert exchange_counts(st, comps, 200)["live_licences"] == 1
    assert exchange_counts(st, comps, 5000)["live_licences"] == 0  # past the deadline
    assert letter(0) == "A" and letter(3) == "D"


# ============================================================================================ events
def test_events_cover_every_kind_at_its_exact_tick_in_order():
    ev = story.events_of(_state())
    kinds = [(e.t, e.kind) for e in ev]
    assert kinds == sorted(kinds, key=lambda x: x[0])
    k = {e.kind: e for e in ev}
    assert (k["licence_signed"].t, k["licence_signed"].a, k["licence_signed"].b) == (110, "0", "1")
    assert {(e.ref, e.kind) for e in ev if e.ref.startswith("O")} == {
        ("O1", "paid"), ("O2", "late"), ("O3", "broken"), ("O4", "collected")}  # O5 replaced: nothing
    plain = [e for e in ev if e.kind.startswith("plain")]
    assert [(e.t, e.kind) for e in plain] == [(50, "plain"), (3700, "plain_late")]  # after the break: late
    assert plain[1].text == "O3 late, sorry"
    assert k["tender_won"].t == 200 and k["tender_broken"].t == 3000
    assert sum(e.kind == "message" for e in ev) == 2 and sum(e.kind == "offer" for e in ev) == 1


def test_licences_use_the_latest_terms_end_at_settlement_and_clip_to_the_grantor_region():
    lics = story.licences_of(_state(), lambda cid, t: [[0, 0, 25, 25]] if cid == "1" else [])
    assert len(lics) == 1
    lc = lics[0]
    assert (lc.grantor, lc.grantee, lc.start, lc.end, lc.fee) == ("1", "0", 110, 4000, 300)
    assert lc.rect == [10, 10, 25, 20]  # terms_history widened it to x 30; the region stops at 25
    assert story.clip_rect([0, 0, 5, 5], []) == [0, 0, 5, 5]


# ============================================================================================ excerpts
@pytest.mark.parametrize("text", ["see /home/x/notes.txt", "in /private/var/x", "https://example.com", "mail me a@b.c",
                                  "key sk-abc123", "run `rh wait`", "", "emoji \U0001f600 here ok fine"])
def test_excerpt_refuses_unsafe_text(text):
    assert story.excerpt(text) is None


def test_excerpt_drops_signatures_and_ticks_and_cuts_at_a_word():
    assert story.excerpt("Company B: happy to license that corner to you. More later.") == \
        "happy to license that corner to you."
    assert story.excerpt("sorry, I missed the instalment due at tick 26199 - paid now.") == \
        "sorry, I missed the instalment - paid now."
    long = "word " * 40
    out = story.excerpt(long, limit=30)
    assert len(out) <= 30 and out.endswith("…") and "  " not in out
    assert "$" not in story.excerpt("costs $5 per unit for the whole route here")  # no mathtext


# ============================================================================================ timing
def test_warp_schedule_is_monotone_slow_near_key_ticks_and_fast_in_the_run_off():
    t0, horizon, t1 = 0, 400 * D, 600 * D
    s = story.warp_schedule(900, t0, t1, horizon, [100 * D], hold_start=20, hold_end=30)
    assert len(s) == 900 and s[:20] == [0.0] * 20 and s[-30:] == [float(t1)] * 30
    assert all(b >= a for a, b in itertools.pairwise(s))

    def frames_in(lo, hi):
        return sum(lo <= x < hi for x in s)

    assert frames_in(95 * D, 105 * D) > 2 * frames_in(295 * D, 305 * D)  # around the key moment: slower
    assert frames_in(295 * D, 305 * D) > 2 * frames_in(495 * D, 505 * D)  # run-off: faster
    with pytest.raises(ValueError):
        story.warp_schedule(10, 0, 100, 50, [], 5, 5)


def test_frames_of_maps_each_event_to_the_first_frame_that_shows_it():
    sched = [0.0, 0.0, 10.0, 20.0, 30.0]
    ev = [story.Event(0, "x"), story.Event(15, "x"), story.Event(99, "x")]
    assert story.frames_of(ev, sched) == [0, 3, 4]


# ============================================================================================ contract layer
def _layer_inputs():
    st = _state()
    lics = story.licences_of(st)
    ev = story.events_of(st)
    sched = [float(t) for t in range(0, 6000, 10)]  # one frame per 10 ticks
    return lics, ev, story.frames_of(ev, sched), sched


def test_pulse_runs_along_its_arc_in_video_time_then_stops():
    lics, ev, frames, sched = _layer_inputs()
    f_paid = frames[[e.ref for e in ev].index("O1")]
    at = story.contract_layer(f_paid + 15, sched[f_paid + 15], lics, ev, frames, fps=30, pulse_s=1.0)
    p = [x for x in at.pulses if x.kind == "paid"]
    assert len(p) == 1 and p[0].licence == "C1" and p[0].progress == pytest.approx(0.5)
    after = story.contract_layer(f_paid + 31, sched[f_paid + 31], lics, ev, frames, fps=30, pulse_s=1.0)
    assert not [x for x in after.pulses if x.kind == "paid"]


def test_a_break_flashes_then_leaves_a_scar_and_an_ended_licence_stays_as_a_ghost():
    lics, ev, frames, sched = _layer_inputs()
    fb = frames[[e.ref for e in ev].index("O3")]
    now = story.contract_layer(fb, sched[fb], lics, ev, frames)
    assert now.scars == {"C1": ["broken"]} and now.flashes and now.flashes[0][2] == pytest.approx(1.0)
    later = story.contract_layer(fb + 200, sched[fb + 200], lics, ev, frames)
    assert later.scars == {"C1": ["broken"]} and not later.flashes
    lc, inten = later.glowing[0]
    assert sched[fb + 200] >= lc.end and inten == pytest.approx(0.22)  # ghost after its end
    before = story.contract_layer(5, sched[5], lics, ev, frames)
    assert before.glowing == [] and before.scars == {}


def test_tender_markers_show_their_outcome_only_after_settlement():
    lics, ev, frames, sched = _layer_inputs()
    f_award = frames[[e.kind for e in ev].index("tender_won")]
    a = story.contract_layer(f_award + 1, sched[f_award + 1], lics, ev, frames)
    assert [(e.ref, o) for e, _, o in a.tenders] == [("T1", None)]
    b = story.contract_layer(len(sched) - 1, sched[-1], lics, ev, frames)
    assert [(e.ref, o) for e, _, o in b.tenders] == [("T1", "broken")]


# ============================================================================================ captions, camera
def test_captions_mark_the_first_moments_never_overlap_and_quote_safely():
    lics, ev, frames, sched = _layer_inputs()
    caps = story.captions_of(ev, frames, lics, {"0": "A", "1": "B"}, horizon=4500, sched=sched, fps=30, hold_s=1.0)
    titles = [c.title for c in caps]
    assert titles[0] == "First contract signed" and "First broken promise" in titles and "Decisions end" in titles
    for a, b in itertools.pairwise(caps):
        assert b.frame >= a.frame + a.frames
    first = caps[0]
    assert first.quote == "happy to license that corner to you." and first.who == "1"
    broken = next(c for c in caps if c.title == "First broken promise")
    assert broken.quote == "sorry, I missed the instalment." and "A misses" in broken.body
    plain = story.captions_of(ev, frames, lics, {"0": "A", "1": "B"}, 4500, sched, fps=30, quotes=False, hold_s=1.0)
    assert all(c.quote is None for c in plain)
    c, alpha = story.caption_at(caps, first.frame)
    assert c is first and 0 < alpha < 1
    assert story.caption_at(caps, first.frame + first.frames // 2)[1] == pytest.approx(1.0)


def test_camera_eases_to_a_caption_focus_and_never_leaves_the_map():
    cap = story.Caption(frame=100, frames=60, title="x", focus=(250.0, 10.0))
    assert story.camera_at(0, [cap], (256, 256)) == (128, 128, 1.0)
    cx, cy, z = story.camera_at(130, [cap], (256, 256), zoom=1.5)
    assert z == pytest.approx(1.5)
    half = 256 / 2 / z
    assert cx == pytest.approx(256 - half) and cy == pytest.approx(half)  # clamped at the corner
    mid = story.camera_at(85, [cap], (256, 256), zoom=1.5)[2]
    assert 1.0 < mid < 1.5
    assert story.camera_at(200, [cap], (256, 256)) == (128, 128, 1.0)


def test_geometry_helpers():
    pts = story.bezier((0, 0), (10, 0), 0.25, 10)
    assert pts[0] == (0, 0) and pts[-1] == (10, 0) and min(y for _, y in pts) == pytest.approx(0)
    assert max(y for _, y in pts) > 1  # bowed to one side
    assert story.point_on(pts, 0) == (0, 0) and story.point_on(pts, 1) == (10, 0)
    assert story.region_centre([[0, 0, 9, 9], [10, 0, 29, 9]]) == pytest.approx((14.5, 4.5))


# ============================================================================================ scene helpers
def test_scene_helpers():
    from railhead.cinema.scene import layout, pretty_model, rank_order, rect_px, stations_outside

    assert pretty_model("claude", "claude-opus-5-5") == "Claude Opus 5.5"
    assert pretty_model("codex", "gpt-6.1-terra") == "Codex gpt-6.1-terra"
    assert rect_px([10, 20, 19, 29], 256) == (256 - 1 - 19 - 0.5, 19.5, 10, 10)  # x mirrored like stations
    vals = {"a": 100.0, "b": 99.0, "c": 50.0}
    assert rank_order(vals, {}, 5) == ["a", "b", "c"]
    assert rank_order({"a": 99.0, "b": 100.0, "c": 50.0}, {"a": 0, "b": 1, "c": 2}, 5) == ["a", "b", "c"]
    assert rank_order({"a": 80.0, "b": 100.0, "c": 50.0}, {"a": 0, "b": 1, "c": 2}, 5) == ["b", "a", "c"]
    sample = {"co": {"0": {"st": [[1, 5, 5], [2, 50, 50]]}, "1": {"st": [[3, 60, 60]]}}}
    assert stations_outside(sample, {"0": [[0, 0, 9, 9]], "1": [[55, 55, 70, 70]]}, {"0": [[45, 45, 55, 55]]}) == 0
    assert stations_outside(sample, {"0": [[0, 0, 9, 9]], "1": []}, {}) == 2
    for kind, w, h, n in (("split", 1920, 1080, 2), ("square", 1080, 1080, 2), ("main", 1920, 1080, 1)):
        L = layout(kind, w, h, n)
        for P in L["panels"]:
            for b in (P["map"], P["bars"], P["counters"], P["caption"]):
                assert 0 <= b.x and b.x + b.w <= w + 0.5 and 0 <= b.y and b.y + b.h <= h + 0.5, (kind, b)
            assert not P["map"].overlaps(P["bars"]) and not P["map"].overlaps(P["counters"])
        if n == 2:
            assert not L["panels"][0]["map"].overlaps(L["panels"][1]["map"])


# ============================================================================================ one frame
def _doc(arm: str, scale: int) -> dict:
    comps = [{"id": 0, "slot": 0, "name": "Company A", "letter": "A", "vendor": "claude", "model": "claude-opus-5-5"},
             {"id": 1, "slot": 1, "name": "Company B", "letter": "B", "vendor": "codex", "model": "gpt-6.1-terra"}]
    samples = []
    for i, t in enumerate(range(0, 6000, 200)):
        co = {}
        for c in ("0", "1"):
            st = [[1, 5, 5], [2, 20, 8]] if c == "0" else [[3, 40, 40], [4, 50, 60]]
            co[c] = {"cash": 1000 * i * scale, "loan": 500, "nw": 1000 * i * scale - 500, "ledger": 0,
                     "st": st if i > 2 else [], "rt": [["road", st[0][0], st[1][0], 2]] if i > 2 else [],
                     "veh": {"road": 2, "rail": 0, "air": 0, "water": 0}}
        samples.append({"t": t, "date": "2000-01-01", "phase": "play" if t < 4500 else "runoff", "why": "grid",
                        "co": co, "ex": {"signed": 1, "live_licences": 1, "messages": 2, "obligations_broken": 1}})
    return {"version": 1, "arm": arm, "base": {"size_x": 64, "size_y": 64, "start_ymd": "2000-01-01",
                                               "start_ticks": 0},
            "start_ticks": 0, "horizon_ticks": 4500, "end_ticks": samples[-1]["t"], "horizon_days": 60,
            "companies": comps, "regions": {"map": [64, 64], "regions": {"0": [[0, 0, 31, 63]], "1": [[32, 0, 63, 63]]},
                                            "types": {"0": 0, "1": 1}, "v": 1},
            "industries": {"5": [45, 45, 0]}, "exchange": _state(), "samples": samples,
            "check": {"ok": True, "mismatches": []}}


def test_one_frame_renders_from_synthetic_samples(tmp_path):
    pytest.importorskip("matplotlib")
    import matplotlib.image as mpimg
    import numpy as np

    from railhead.cinema.scene import Arm, Scene, captions_for, check_overlay, schedule_for
    from railhead.timelapse import _png

    (tmp_path / "base").mkdir()
    img = np.zeros((64, 64, 3))
    img[..., 2] = 0.8  # water
    img[10:50, 10:50] = [0.3, 0.6, 0.2]  # land
    mpimg.imsave(tmp_path / "base" / "minimap.png", img)
    arms = [Arm(_doc("enforced", 1)), Arm(_doc("cheap_talk", 2))]
    assert check_overlay(arms)["enforced"] >= 0
    sched = schedule_for(arms, seconds=6, hold_start=0.5, hold_end=0.5)
    caps = captions_for(arms, sched, quotes=True, hold_s=1.0)
    for kind, w, h, aa in (("split", 1920, 1080, arms), ("square", 1080, 1080, arms), ("main", 1920, 1080, arms[:1])):
        scene = Scene(aa, kind, "dark", w, h, 1.0, sched, caps[: len(aa)], tmp_path / "base" / "minimap.png")
        try:
            for f in (0, len(sched) // 2, len(sched) - 1):
                buf = scene.frame(f)
                assert np.asarray(buf).shape == (h, w, 4)
            out = tmp_path / f"{kind}.png"
            _png(buf, out, w, h)
            assert mpimg.imread(out).shape[:2] == (h, w)
        finally:
            scene.close()
    json.dumps(_doc("x", 1))  # the sample doc shape is plain JSON


# ============================================================================================ engine shots
def test_save_plan_finds_the_first_licence_and_the_first_break():
    from railhead.cinema.shots import save_plan

    recs = [{"kind": "ex", "ticks": 100, "x": {"op": "offer", "contract_id": "C1", "kind": "supply"}},
            {"kind": "ex", "ticks": 110, "x": {"op": "accept", "contract_id": "C1"}},
            {"kind": "ex", "ticks": 200, "x": {"op": "offer", "contract_id": "C2", "kind": "licence"}},
            {"kind": "ex", "ticks": 210, "x": {"op": "accept", "contract_id": "C2"}},
            {"kind": "ex", "ticks": 900, "x": {"op": "obligation_settle", "outcome": "kept"}},
            {"kind": "ex", "ticks": 950, "x": {"op": "obligation_settle", "outcome": "broken"}},
            {"kind": "ex", "ticks": 990, "x": {"op": "obligation_settle", "outcome": "broken"}}]
    assert save_plan(recs) == {"licence": 210 + 60 * D, "broken": 950}
    assert save_plan([]) == {}


def test_iso_px_maps_tiles_onto_the_giant_screenshot():
    from railhead.cinema.shots import crop_at, iso_px

    w, h = 16257, 8255  # the engine's giant screenshot of a 256 x 256 map
    left, top = iso_px(255, 0, w, h, 256, 256), iso_px(0, 0, w, h, 256, 256)
    right, bottom = iso_px(0, 255, w, h, 256, 256), iso_px(255, 255, w, h, 256, 256)
    assert abs(left[0]) < 64 and abs(right[0] - w) < 64  # west and east corners at the edges
    assert abs(top[0] - w / 2) < 64 and 0 <= top[1] < 200  # north at the top centre
    assert abs(bottom[1] - h) < 64
    np = pytest.importorskip("numpy")
    img = np.zeros((300, 400, 3))
    assert crop_at(img, 0, 0, 8, 8, 100, 50).shape == (50, 100, 3)  # clamped inside the image


def test_sites_pick_the_licensee_stations_and_inserts_follow_the_captions():
    from railhead.cinema.scene import inset_for, ken_burns
    from railhead.cinema.shots import sites

    doc = _doc("cheap_talk", 1)
    doc["saves"] = {"licence": 500, "broken": 3500, "horizon": 4500}
    doc["samples"][2]["co"]["0"]["st"] = [[9, 12, 14], [10, 14, 16]]  # A's stations on its site in B's land
    out = sites(doc)
    assert [s["name"] for s in out] == ["licence-C1", "broken-C1", "horizon-C1"]
    assert out[0]["tile"] == 15 * 64 + 13 and (out[0]["grantee"], out[0]["grantor"]) == ("0", "1")
    assert out[2]["tile"] == 15 * 64 + 20  # no station on the site then: the centre of the (amended) site
    shots = [s | {"img": None} for s in out]
    assert inset_for(shots, "licence_signed")["name"] == "licence-C1"
    assert inset_for(shots, "partial")["name"] == "broken-C1" and inset_for(shots, "paid") is None
    np = pytest.importorskip("numpy")
    img = np.zeros((540, 960, 3))
    assert ken_burns(img, 0).shape[:2] == (335, 595) and ken_burns(img, 1).shape[:2] == (270, 480)
