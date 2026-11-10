"""`railhead stats`: multi-map aggregation with map-level bootstrap intervals (no engine)."""

import json
import math

from railhead import stats


def _row(player, nw, end="submitted", status="verified", valid=1, k50=None):
    r = {"player": player, "status": status, "net_worth": nw, "valid": valid, "end": end}
    if k50 is not None:
        r["score_k50"] = k50
    return r


def _maps(table, ends=None):
    """table: {map: {player: net worth or [net worths]}} -> list of export docs."""
    docs = []
    for m, row in table.items():
        res = []
        for p, v in row.items():
            for nw in (v if isinstance(v, list) else [v]):
                res.append(_row(p, nw, end=(ends or {}).get((m, p), "submitted")))
        res.append({"player": "baseline", "status": "certificate", "net_worth": 100_000})
        docs.append({"task": m, "results": res})
    return docs


def test_known_mean_difference_is_recovered_and_not_tied():
    # b = a + 2M on every map: the paired difference is exactly 2M and the interval is [2M, 2M].
    a = [1e6, 5e6, 2e7, 3e5, 8e6, 1.2e7]
    docs = _maps({f"m{i}": {"a": x, "b": x + 2e6} for i, x in enumerate(a)})
    agg = stats.aggregate(docs, boot=2000, seed=1)
    pair = next(p for p in agg["pairs"] if {p["a"], p["b"]} == {"a", "b"})
    sign = 1 if pair["a"] == "b" else -1
    assert pair["n"] == 6
    assert math.isclose(sign * pair["net_worth"]["mean"], 2e6)
    assert math.isclose(sign * pair["net_worth"]["lo"], 2e6) and math.isclose(sign * pair["net_worth"]["hi"], 2e6)
    assert pair["net_worth"]["tied"] is False
    assert math.isclose(pair["sign_p"], 2 * 0.5 ** 6)  # all six maps favour b


def test_identical_players_are_tied_and_interval_covers_zero():
    import random

    rng = random.Random(3)
    docs = _maps({f"m{i}": {"a": rng.uniform(1e5, 1e7), "b": 0.0} for i in range(8)})
    for d in docs:  # b gets a's value +/- noise that cancels on average
        a = next(r for r in d["results"] if r["player"] == "a")
        b = next(r for r in d["results"] if r["player"] == "b")
        b["net_worth"] = a["net_worth"] + (1e5 if int(d["task"][1:]) % 2 else -1e5)
    agg = stats.aggregate(docs, boot=2000, seed=0)
    pair = agg["pairs"][0]
    for metric in ("net_worth", "asinh"):
        assert pair[metric]["lo"] <= 0 <= pair[metric]["hi"]
        assert pair[metric]["tied"] is True
    assert pair["sign_p"] == 1.0


def test_interval_stays_inside_min_and_max_of_map_differences():
    docs = _maps({"m0": {"a": 1e6, "b": 2e6}, "m1": {"a": 1e6, "b": 5e6}, "m2": {"a": 3e6, "b": 3.5e6}})
    pair = stats.aggregate(docs, boot=3000, seed=7)["pairs"][0]
    diffs = sorted([1e6, 4e6, 0.5e6])
    sign = 1 if pair["a"] == "b" else -1
    lo, hi = sorted([sign * pair["net_worth"]["lo"], sign * pair["net_worth"]["hi"]])
    assert diffs[0] - 1e-6 <= lo <= hi <= diffs[-1] + 1e-6


def test_resampling_is_by_map_not_by_game():
    # On m0 player a has three games; they are averaged into one cell, so m0 counts once.
    docs = _maps({"m0": {"a": [1e6, 2e6, 3e6], "b": 1e6}, "m1": {"a": 1e6, "b": 1e6}})
    cells = stats.cells(docs)
    assert cells["m0"]["a"]["net_worth"] == 2e6 and cells["m0"]["a"]["games"] == 3
    agg = stats.aggregate(docs, boot=500, seed=0)
    pair = agg["pairs"][0]
    assert pair["n"] == 2  # two maps, not four games
    a_sum = next(s for s in agg["players"] if s["player"] == "a")
    assert a_sum["n_maps"] == 2 and a_sum["n_games"] == 4
    assert math.isclose(a_sum["mean_net_worth"], 1.5e6)  # mean of map cells (2M, 1M), not of games
    # the bootstrap draws the same map indices for both players: mean diff over maps is 0.5M
    sign = 1 if pair["a"] == "a" else -1
    assert math.isclose(sign * pair["net_worth"]["mean"], 0.5e6)


def test_two_documents_for_one_map_pool_their_games():
    # e.g. arena2.json and arena2-free.json both cover arena-dev-0100
    docs = _maps({"m0": {"a": 1e6}}) + _maps({"m0": {"a": [3e6, 5e6]}})
    cell = stats.cells(docs)["m0"]["a"]
    assert cell["games"] == 3 and math.isclose(cell["net_worth"], 3e6)


def test_missing_cells_are_reported_per_pair_not_dropped():
    docs = _maps({"m0": {"a": 1e6, "b": 2e6, "c": 3e6}, "m1": {"a": 1e6, "b": 2e6}, "m2": {"a": 1e6, "c": 4e6}})
    agg = stats.aggregate(docs, boot=200, seed=0)
    n = {frozenset((p["a"], p["b"])): p["n"] for p in agg["pairs"]}
    assert n[frozenset("ab")] == 2 and n[frozenset("ac")] == 2 and n[frozenset("bc")] == 1
    by = {s["player"]: s for s in agg["players"]}
    assert by["a"]["n_maps"] == 3 and by["b"]["n_maps"] == 2 and by["c"]["n_maps"] == 2
    assert by["a"]["maps"] == ["m0", "m1", "m2"]


def test_pair_with_no_shared_map_is_listed_with_n_zero():
    docs = _maps({"m0": {"a": 1e6}, "m1": {"b": 2e6}})
    pair = stats.aggregate(docs, boot=100, seed=0)["pairs"][0]
    assert pair["n"] == 0 and pair["net_worth"] is None and pair["sign_p"] is None


def test_invalid_and_rejected_games_are_excluded_with_a_reason():
    docs = _maps({"m0": {"a": 1e6, "b": 2e6}})
    docs[0]["results"].append(_row("c", 9e9, valid=0))
    docs[0]["results"].append({"player": "d", "status": "rejected", "valid": 1})
    docs[0]["results"].append({"player": "e", "status": "playing", "net_worth_live": 5})
    agg = stats.aggregate(docs, boot=100, seed=0)
    assert {s["player"] for s in agg["players"]} == {"a", "b"}
    reasons = {(x["map"], x["player"]): x["reason"] for x in agg["excluded"]}
    assert reasons[("m0", "c")] == "valid=0" and reasons[("m0", "d")] == "rejected"
    assert reasons[("m0", "e")] == "not verified (playing)"


def test_finished_only_drops_stopped_games():
    docs = _maps({"m0": {"a": 1e6, "b": 2e6}, "m1": {"a": 1e6, "b": 2e6}},
                 ends={("m1", "b"): "stopped"})
    agg = stats.aggregate(docs, boot=100, seed=0, finished_only=True)
    assert agg["pairs"][0]["n"] == 1
    assert any(x["reason"] == "end=stopped" for x in agg["excluded"])
    agg = stats.aggregate(docs, boot=100, seed=0, finished_only=False)
    assert agg["pairs"][0]["n"] == 2


def test_sign_test_drops_zero_differences():
    assert stats.sign_test([0.0, 0.0, 1.0]) == (1.0, 1)
    p, n = stats.sign_test([1.0] * 5)
    assert n == 5 and math.isclose(p, 0.0625)
    assert stats.sign_test([]) == (None, 0)


def test_ranking_marks_ties_explicitly():
    # a >> b on every map (separated); b and c identical up to cancelling noise (tied).
    docs = _maps({f"m{i}": {"a": 5e7, "b": 1e6 + (1e4 if i % 2 else -1e4), "c": 1e6} for i in range(6)})
    rank = stats.aggregate(docs, boot=1000, seed=0)["ranking"]
    assert rank[0]["player"] == "a" and rank[0]["tied_with"] == []
    assert {r["player"] for r in rank[1:]} == {"b", "c"}
    assert all(r["tied_with"] == [o] for r, o in zip(rank[1:], [rank[2]["player"], rank[1]["player"]], strict=True))


def test_asinh_and_score_means():
    docs = _maps({"m0": {"a": 1e5}, "m1": {"a": -1e5}})
    docs[0]["results"][0]["score_k50"] = 0.4
    docs[1]["results"][0]["score_k50"] = 0.0
    s = stats.aggregate(docs, boot=10, seed=0)["players"][0]
    assert math.isclose(s["mean_asinh"], 0.0) and math.isclose(s["median_net_worth"], 0.0)
    assert math.isclose(s["mean_score_k50"], 0.2)


def test_maps_document_with_several_tasks_and_player_filter(tmp_path):
    many = {"maps": _maps({"m0": {"a": 1e6, "b": 2e6}, "m1": {"a": 1e6, "b": 3e6}})}
    one = _maps({"m2": {"a": 1e6, "b": 1e6, "z": 4e6}})[0]
    (tmp_path / "many.json").write_text(json.dumps(many))
    (tmp_path / "one.json").write_text(json.dumps(one))
    docs = stats.load_docs([tmp_path / "many.json", tmp_path / "one.json"])
    assert [d["task"] for d in docs] == ["m0", "m1", "m2"]
    agg = stats.aggregate(docs, boot=10, seed=0, players=["a", "b"])
    assert {s["player"] for s in agg["players"]} == {"a", "b"}


def test_end_state_from_the_action_log(tmp_path):
    def log(recs):
        p = tmp_path / f"{len(list(tmp_path.iterdir()))}.jsonl"
        p.write_text("".join(json.dumps(r) + "\n" for r in recs))
        return p

    head = {"kind": "header", "v": 1, "company": 0, "ticks": 0, "checksum": {"date": 1000}}

    def wait(d):
        return {"kind": "wait", "seq": 1, "ticks": 0, "end_ticks": 1, "a": {"days": 1}, "checksum": {"date": d},
                "snap": {"date": "x"}}

    submit = {"kind": "meta", "seq": 2, "ticks": 1, "m": "submit", "a": {}}
    assert stats.end_state(log([head, wait(1100), submit]), horizon_days=3650)["end"] == "submitted"
    assert stats.end_state(log([head, wait(4650)]), horizon_days=3650)["end"] == "horizon"
    e = stats.end_state(log([head, wait(2000)]), horizon_days=3650)
    assert e["end"] == "stopped" and e["days_played"] == 1000


def test_cli_prints_and_exports(tmp_path, capsys):
    (tmp_path / "a.json").write_text(json.dumps({"maps": _maps({"m0": {"a": 1e6, "b": 2e6},
                                                                  "m1": {"a": 2e6, "b": 3e6}})}))
    out = tmp_path / "agg.json"
    assert stats.main([str(tmp_path / "a.json"), "--out", str(out), "--boot", "200"]) == 0
    text = capsys.readouterr().out
    assert "paired" in text and "a" in text
    doc = json.loads(out.read_text())
    assert {"players", "pairs", "ranking", "excluded", "maps"} <= set(doc)
    assert stats.main([]) == 2
