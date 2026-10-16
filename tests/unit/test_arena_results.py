"""Arena results: verified rows from proof-of-play verdicts, live rows from the action log, baselines from the task."""

import json

from railhead.arena import results


def _player(root, name, snaps, reward=None):
    run = root / name / "state/run"
    run.mkdir(parents=True)
    lines = [{"kind": "header", "ticks": 0}, {"kind": "act", "m": "x", "ok": True}]
    lines += [{"kind": "wait", "snap": s} for s in snaps]
    (run / "actions.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    if reward:
        (root / name / "verify").mkdir()
        (root / name / "verify/reward.json").write_text(json.dumps(reward))


def test_results_rank_verified_players_and_add_the_certified_baselines(tmp_path):
    task = tmp_path / "task"
    task.mkdir()
    (task / "task.json").write_text(json.dumps({"objective": {"w_idle": -100, "w_base": 900, "score": "log50"}}))
    arena = tmp_path / "arena"
    _player(arena, "a", [{"date": "1961-01-01", "net_worth": 50, "vehicles": 2, "stations": 1}],
            {"net_worth": 5900, "net_worth_horizon": 4000, "acts": 10, "failed_acts": 1, "waits": 1,
             "divergences": 0, "valid": 1})
    _player(arena, "b", [{"date": "1961-01-01", "net_worth": 20, "vehicles": 9, "stations": 3}],
            {"net_worth": 900, "net_worth_horizon": 800, "acts": 30, "failed_acts": 0, "waits": 2,
             "divergences": 0, "valid": 1})
    _player(arena, "c", [{"date": "1960-06-30", "net_worth": -400, "vehicles": 1, "stations": 1}])

    rows = results(arena, task)

    # by net worth (a model before a baseline on a tie), then the games that are not verified yet
    assert [r["player"] for r in rows] == ["a", "b", "baseline", "idle", "c"]
    a = rows[0]
    assert a["status"] == "verified" and a["gain_x_baseline"] == 6.0 and a["vehicles"] == 2
    assert a["net_worth"] == 5900 and a["net_worth_horizon"] == 4000 and 0 < a["score_k50"] <= 1
    assert rows[2] == {"player": "baseline", "status": "certificate", "net_worth": 900, "gain_x_baseline": 1.0}
    assert rows[3] == {"player": "idle", "status": "certificate", "net_worth": -100, "gain_x_baseline": 0.0}
    assert rows[4]["status"] == "playing" and rows[4]["net_worth_live"] == -400


def test_fleet_mix_counts_bought_and_cloned_vehicles_by_mode(tmp_path):
    from railhead.arena import engine_mode, fleet_mix

    assert [engine_mode(e) for e in (0, 115, 116, 203, 204, 214, 215, 255)] == \
        ["rail", "rail", "road", "road", "water", "water", "air", "air"]
    log = tmp_path / "actions.jsonl"
    rows = [{"kind": "header"},
            {"kind": "act", "m": "vehicle_buy", "ok": True, "a": {"engine": 123}, "ret": {"vehicle": 13, "engine": 123}},
            {"kind": "act", "m": "vehicle_clone", "ok": True, "a": {"vehicle": 13}, "ret": {"vehicle": 14}},
            {"kind": "act", "m": "vehicle_clone", "ok": False, "a": {"vehicle": 13}, "code": "ERR_NOT_ENOUGH_CASH"},
            {"kind": "act", "m": "vehicle_buy", "ok": True, "a": {"engine": 220}, "ret": {"vehicle": 15, "engine": 220}},
            {"kind": "act", "m": "vehicle_buy", "ok": True, "a": {"engine": 5, "dry_run": True}, "ret": {"cost": 1}}]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert fleet_mix(log) == {"road": 2, "rail": 0, "air": 1, "water": 0}
