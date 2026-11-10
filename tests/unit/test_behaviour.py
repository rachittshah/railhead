"""Behaviour metrics from small fake action logs (no engine)."""

import json

from railhead.behaviour import DEFINITIONS, analyse, analyse_log, scalars, transcript_forks

T0 = 1000  # start ticks; 74 ticks per game day
DAY = 74


def _write(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))
    return path


def _act(m, a, ok=True, ret=None, code=None, day=0):
    r = {"kind": "act", "m": m, "a": a, "ok": ok, "ticks": T0 + day * DAY}
    if ok:
        r["ret"] = ret or {"cost": 0}
    else:
        r["code"] = code
    return r


def _wait(day0, day1, snap):
    return {"kind": "wait", "ticks": T0 + day0 * DAY, "end_ticks": T0 + day1 * DAY,
            "checksum": {"ticks": T0 + day1 * DAY, "date": 700000 + day1}, "snap": snap}


HEADER = {"kind": "header", "v": 1, "ticks": T0, "checksum": {"date": 700000, "ticks": T0}}


def _game(tmp_path, submit=True):
    recs = [
        HEADER,
        _act("company_loan", {"amount": 100000}, ret={"cost": 0, "loan": 100000}),
        _act("air_airport", {"tile": 5, "type": "small", "dry_run": True}, ret={"cost": 900, "dry_run": True}),
        _act("air_airport", {"tile": 5, "type": "small"}, ret={"cost": 1000, "station": 0}),
        _act("air_airport", {"tile": 900, "type": "small"}, ret={"cost": 1000, "station": 1}),
        _act("vehicle_buy", {"engine": 220, "depot_tile": 6}, ret={"cost": 5000, "engine": 220, "vehicle": 1}),
        _act("order_append", {"vehicle": 1, "tile": 5}),
        _act("order_append", {"vehicle": 1, "tile": 900}),
        _act("vehicle_start", {"vehicle": 1}),
        _act("vehicle_clone", {"vehicle": 1, "depot_tile": 6, "share_orders": True}, ret={"cost": 5000, "vehicle": 2}),
        _act("vehicle_start", {"vehicle": 2}),
        # a rail attempt that fails, then a road route
        _act("rail_line", {"from_tile": 10, "to_tile": 20}, ok=False, code="ERR_AREA_NOT_CLEAR"),
        _act("rail_line", {"from_tile": 10, "to_tile": 30}, ok=False, code="ERR_AREA_NOT_CLEAR"),
        _act("road_line", {"from_tile": 40, "to_tile": 50}, ret={"cost": 300}),
        _wait(0, 100, {"loan": 100000, "income_q": 100, "expenses_q": -500}),
        _act("vehicle_buy", {"engine": 120, "depot_tile": 41}, ret={"cost": 400, "engine": 120, "vehicle": 3}, day=100),
        _act("order_append", {"vehicle": 3, "tile": 40}, day=100),
        _act("order_append", {"vehicle": 3, "tile": 50}, day=100),
        _act("vehicle_start", {"vehicle": 3}, day=100),
        _wait(100, 400, {"loan": 100000, "income_q": 9000, "expenses_q": -1000}),
        _act("company_loan", {"amount": 0}, ret={"cost": 0, "loan": 0}, day=400),
        _act("vehicle_sell", {"vehicle": 2}, ret={"cost": -3000, "vehicle": 2}, day=400),
        _wait(400, 500, {"loan": 0, "income_q": 9000, "expenses_q": -1000}),
    ]
    if submit:
        recs.append({"kind": "meta", "m": "submit", "a": {"confirm": "yes"}, "ticks": T0 + 500 * DAY})
    return _write(tmp_path / "p" / "state" / "run" / "actions.jsonl", recs)


def test_vehicles_and_spend_by_mode_total_and_per_year(tmp_path):
    b = analyse_log(_game(tmp_path), horizon_days=1825)
    # original engine set: < 116 rail, < 204 road, < 215 water, < 256 air
    assert b["vehicles_by_mode"] == {"road": 1, "rail": 0, "air": 2, "water": 0} and b["vehicles_total"] == 3
    assert b["vehicle_spend_by_mode"]["air"] == 10000 and b["vehicle_spend_by_mode"]["road"] == 400
    assert b["vehicles_by_mode_year"] == {"1": {"air": 2, "road": 1}}
    assert b["spend_by_mode"]["air"] == 12000  # two airports + two aircraft, dry run excluded
    assert b["spend_by_mode"]["road"] == 700
    assert b["non_air_vehicle_spend_share"] == round(400 / 10400, 4)


def test_first_vehicle_and_first_profitable_route_proxy(tmp_path):
    b = analyse_log(_game(tmp_path), horizon_days=1825)
    assert b["first_vehicle_mode"] == "air"
    # snapshot at day 100 has income+expenses < 0; day 400 is the first positive quarter: 2 air + 1 road running
    assert b["first_profitable_day"] == 400
    assert b["first_profitable_route_mode"] == "air" and b["first_profitable_modes"] == ["air", "road"]


def test_routes_failed_rates_and_attempts(tmp_path):
    b = analyse_log(_game(tmp_path), horizon_days=1825)
    assert b["routes_by_mode"] == {"road": 1, "rail": 0, "air": 1, "water": 0}  # shared orders: one air route
    assert b["actions"] == 19 and b["dry_runs"] == 1 and b["failed"] == 2
    assert b["failed_rate_by_family"]["rail"] == 1.0
    assert b["top_errors"] == {"ERR_AREA_NOT_CLEAR": 2}
    assert b["rail"] == {"tried": 2, "vehicles": 0, "spend": 0, "route": 0, "kept": 0, "status": "tried_no_route"}
    assert b["water"]["status"] == "not_tried"
    assert b["road"]["status"] == "route_kept"
    assert abs(sum(b["action_share_by_family"].values()) - 1) < 1e-3


def test_loan_path_and_end_state(tmp_path):
    b = analyse_log(_game(tmp_path), horizon_days=1825)
    assert b["loan"]["path"] == [[0, 100000], [400, 0]]
    assert b["loan"]["max"] == 100000 and b["loan"]["final"] == 0 and b["loan"]["repaid_day"] == 400
    assert b["end"] == "submitted" and b["days_played"] == 500
    b2 = analyse_log(_game(tmp_path, submit=False), horizon_days=500)
    assert b2["end"] == "horizon"
    b3 = analyse_log(_game(tmp_path, submit=False), horizon_days=1825)
    assert b3["end"] == "stopped"


def test_route_then_sold_counts_as_abandoned(tmp_path):
    recs = [HEADER,
            _act("vehicle_buy", {"engine": 205, "depot_tile": 1}, ret={"cost": 10, "engine": 205, "vehicle": 7}),
            _act("order_append", {"vehicle": 7, "tile": 1}), _act("order_append", {"vehicle": 7, "tile": 2}),
            _act("vehicle_start", {"vehicle": 7}), _act("vehicle_sell", {"vehicle": 7}, ret={"cost": -5})]
    b = analyse_log(_write(tmp_path / "a.jsonl", recs), horizon_days=10)
    assert b["water"]["status"] == "route_then_sold" and b["water"]["route"] == 1 and b["water"]["kept"] == 0
    assert b["routes_by_mode"]["water"] == 0


def test_forks_from_claude_transcript_count_tool_results_once(tmp_path):
    out = '{\n "fork": "a",\n "exact": false,\n "fork_days_left": 730\n}'
    later = '{\n "forks": ["a"],\n "fork_days_left": 640\n}'
    lines = [{"type": "assistant", "session_id": "s", "message": {"content": [
                {"type": "tool_use", "input": {"command": "rh fork create name=a"}}]}},
             {"type": "user", "session_id": "s", "message": {"content": [{"type": "tool_result", "content": out}]},
              "tool_use_result": {"stdout": out}},
             {"type": "user", "session_id": "s", "message": {"content": [{"type": "tool_result", "content": later}]},
              "tool_use_result": {"stdout": later}},
             {"type": "system", "session_id": "s", "text": "help: rh fork create|list name=F"}]
    log = tmp_path / "agent.log"
    log.write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    assert transcript_forks(log, 730) == {"forks_created": 1, "fork_days_used": 90, "source": "agent.log"}
    assert transcript_forks(tmp_path / "missing.log", 730)["forks_created"] is None


def test_forks_from_codex_text_transcript(tmp_path):
    log = tmp_path / "agent.log"
    log.write_text("exec\n/bin/zsh -lc 'rh fork create name=probe' in /x\n succeeded in 0ms:\n"
                   '{\n "fork": "probe",\n "exact": false,\n "fork_days_left": 730\n}\n'
                   "exec\n/bin/zsh -lc 'rh fork wait name=probe days=30' in /x\n succeeded in 0ms:\n"
                   '{\n "days": 30,\n "fork_days_left": 700\n}\n')
    assert transcript_forks(log, 730)["forks_created"] == 1
    assert transcript_forks(log, 730)["fork_days_used"] == 30


def test_analyse_joins_verify_and_claude_usage_and_scalars_are_flat(tmp_path):
    _game(tmp_path)
    tdir = tmp_path / "task"
    tdir.mkdir()
    (tdir / "task.json").write_text(json.dumps({"horizon_days": 1825, "limits": {"max_fork_days": 730}}))
    pdir = tmp_path / "p"
    (pdir / "verify").mkdir()
    (pdir / "verify" / "reward.json").write_text(json.dumps({"net_worth": 5, "net_worth_horizon": 4, "valid": 1,
                                                             "reward": 0.5}))
    (pdir / "agent.log").write_text(json.dumps({"type": "result", "session_id": "s", "num_turns": 7,
                                                "duration_ms": 2500, "total_cost_usd": 0.25,
                                                "usage": {"input_tokens": 10, "output_tokens": 20}}) + "\n")
    b = analyse(pdir, tdir)
    assert b["verified"]["net_worth"] == 5
    assert b["model"]["source"] == "agent.log" and b["model"]["requests"] == 7 and b["model"]["wall_clock_s"] == 2.5
    s = scalars(b)
    assert all(v is None or isinstance(v, (int, float)) for v in s.values())
    assert s["rail_tried"] == 1 and s["rail_route"] == 0 and s["water_tried"] == 0
    assert set(DEFINITIONS) >= {"vehicles_by_mode", "forks_created", "end", "model"}
