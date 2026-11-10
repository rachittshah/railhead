"""Behaviour metrics of one game, read from its action log (plus, when present, its verify result, the agent's
transcript ``agent.log`` and the OTLP sink). No engine is needed.

    railhead ab behaviour <player_dir> --task <task_dir> [--otel DIR] [--json]

``<player_dir>`` holds ``state/run/actions.jsonl`` and, optionally, ``verify/reward.json`` and ``agent.log``.
DEFINITIONS gives a one-line definition of every metric; ``scalars`` flattens one game to the numbers that the
A/B report compares. Modes: road, rail (wagons included), air, water. A "game year" is counted from the start
date of the task: year 1 is days 0-364 of the mandate.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from railhead.actionlog import read_log
from railhead.arena import engine_mode
from railhead.log import event, get_logger

TICKS_PER_DAY = 74
MODES = ("road", "rail", "air", "water")
INFRA_MODE = {"air": "air", "rail": "rail", "road": "road", "water": "water"}  # method prefix -> mode
log = get_logger("behaviour")

DEFINITIONS = {
    "vehicles_by_mode": "vehicles bought or cloned (successful, not dry runs) per mode; rail includes wagons",
    "vehicles_by_mode_year": "the same count per game year (1 = first 365 days of the mandate)",
    "vehicle_spend_by_mode": "money paid for those vehicles (ret.cost of vehicle_buy and vehicle_clone) per mode",
    "vehicle_spend_by_mode_year": "the same money per game year",
    "spend_by_mode": "vehicle spend plus construction spend of that mode (successful air_/rail_/road_/water_ "
                     "actions, ret.cost > 0); bridges, tunnels, land and towns are in 'other'",
    "spend_by_mode_year": "the same money per game year",
    "non_air_vehicle_spend_share": "1 - air vehicle spend / all vehicle spend (None when nothing was bought)",
    "first_vehicle_mode": "mode of the first vehicle bought (successful vehicle_buy)",
    "first_profitable_route_mode": "PROXY (the log has no per-route profit): the mode with the most running route "
                                   "vehicles at the first wait snapshot whose quarter has income_q + expenses_q > 0",
    "first_profitable_modes": "every mode that had a running route vehicle at that snapshot",
    "routes_by_mode": "distinct stop-tile sets (>= 2 distinct order tiles) among vehicles alive at the end of the "
                      "log, per mode; vehicles that share a stop set are one route",
    "action_share_by_family": "share of act records (dry runs included) per method family (prefix before '_')",
    "actions": "act records in the log (dry runs included), as the verifier counts them",
    "dry_runs": "act records with dry_run=true",
    "failed_rate": "act records with ok=false / all act records",
    "failed_rate_by_family": "the same rate per method family",
    "top_errors": "the five most frequent error codes overall",
    "top_errors_by_family": "the three most frequent error codes per family",
    "rail / water": "per mode: tried = act records of that family or vehicle buys of that mode (dry runs and "
                    "failures included); route = a vehicle of that mode had >= 2 distinct order tiles and was "
                    "started; kept = such a vehicle is still alive at the end; status = not_tried | tried_no_route "
                    "| route_then_sold | route_kept",
    "forks_created": "fork_create results ({\"fork\": ..., \"exact\": ...}) in the tool output of the transcript "
                     "(forks are not in the action log; None without a transcript)",
    "fork_days_used": "max_fork_days of the task - the lowest fork_days_left printed in the transcript",
    "loan": "loan path: [game day, loan] at each change (wait snapshots and company_loan results); max, final, "
            "day of the first maximum, day it went back to 0",
    "end": "how the game ended: submitted (rh submit) | horizon (reached the horizon without submit) | stopped "
           "(the log ends before the horizon)",
    "days_played": "game days from the start to the last checksum in the log",
    "model": "LLM requests, input / output / cache-read tokens and cost: from OTLP records of this player and task "
             "inside the cell's time windows, else from Claude result events in agent.log; wall_clock_s = the "
             "harness time windows, else the span of the matched OTLP records, else Claude duration_ms",
    "verified": "net_worth (after run-off), net_worth_horizon, reward, valid from verify/reward.json",
}


def _family(m: str) -> str:
    return m.split("_", 1)[0] if "_" in m else m


def _new_modes(v=0) -> dict:
    return {m: v for m in MODES}


def analyse_log(path: Path, horizon_days: int | None = None) -> dict:
    """Every metric that the action log alone gives (see DEFINITIONS)."""
    recs = [r.raw for r in read_log(Path(path))]
    head = next((r for r in recs if r.get("kind") == "header"), {})
    ck = head.get("checksum") if isinstance(head.get("checksum"), dict) else {}
    t0, d0 = int(ck.get("ticks", head.get("ticks", 0))), ck.get("date")

    def game_day(ticks) -> int:
        return (int(ticks) - t0) // TICKS_PER_DAY

    def year(ticks) -> str:
        return str(game_day(ticks) // 365 + 1)

    acts = dry = failed = 0
    fam_n, fam_fail = Counter(), Counter()
    errors, fam_err = Counter(), defaultdict(Counter)
    bought, spend_v, spend_all = _new_modes(), _new_modes(), {**_new_modes(), "other": 0}
    bought_y, spend_v_y, spend_all_y = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    mode_of: dict[int, str] = {}
    orders: dict[int, list] = {}
    alive: set[int] = set()
    running: set[int] = set()
    wagons: set[int] = set()
    first_vehicle = None
    tried = Counter()
    route_seen: set[str] = set()
    loan_path: list[list[int]] = []
    first_profit = None
    last_day = 0
    submitted = False

    def stops(v: int) -> set:
        return {t for t in (orders.get(v) or []) if t is not None}

    def route_modes_running() -> Counter:
        c = Counter()
        for v in running:
            if v in alive and v in mode_of and len(stops(v)) >= 2:
                c[mode_of[v]] += 1
        return c

    def note_loan(day: int, loan: int) -> None:
        if not loan_path or loan_path[-1][1] != loan:
            loan_path.append([day, int(loan)])

    for r in recs:
        kind = r.get("kind")
        if isinstance(r.get("checksum"), dict) and kind != "header":
            last_day = max(last_day, game_day(r["checksum"].get("ticks", r.get("end_ticks", r.get("ticks", t0)))))
        if kind == "meta" and r.get("m") == "submit":
            submitted = True
        if kind == "wait":
            snap = r.get("snap") or {}
            day = game_day(r.get("end_ticks", r.get("ticks", t0)))
            if "loan" in snap:
                note_loan(day, int(snap["loan"]))
            if first_profit is None and snap and (snap.get("income_q", 0) + snap.get("expenses_q", 0)) > 0:
                first_profit = (day, route_modes_running())
            continue
        if kind != "act":
            continue
        m, a, ret = r.get("m", ""), r.get("a") or {}, r.get("ret") or {}
        fam = _family(m)
        acts += 1
        fam_n[fam] += 1
        engine_m = None
        if m == "vehicle_buy" and (ret.get("engine") is not None or a.get("engine") is not None):
            try:
                engine_m = engine_mode(int(ret.get("engine", a.get("engine"))))
            except (TypeError, ValueError, StopIteration):
                engine_m = None
        if fam in INFRA_MODE:
            tried[INFRA_MODE[fam]] += 1
        elif engine_m:
            tried[engine_m] += 1
        if a.get("dry_run"):
            dry += 1
        if not r.get("ok"):
            failed += 1
            fam_fail[fam] += 1
            errors[r.get("code", "?")] += 1
            fam_err[fam][r.get("code", "?")] += 1
            continue
        if a.get("dry_run"):
            continue
        y = year(r.get("ticks", t0))
        cost = int(ret.get("cost") or 0)
        vmode = None
        if m == "vehicle_buy" and "vehicle" in ret and engine_m:
            vmode = engine_m
            if first_vehicle is None:
                first_vehicle = vmode
        elif m == "vehicle_clone" and "vehicle" in ret:
            vmode = mode_of.get(a.get("vehicle"))
        if vmode:
            v = ret["vehicle"]
            mode_of[v] = vmode
            alive.add(v)
            bought[vmode] += 1
            bought_y[y][vmode] += 1
            if m == "vehicle_clone":
                src = orders.setdefault(a.get("vehicle"), [])
                orders[v] = src if a.get("share_orders") else list(src)
            else:
                orders[v] = []
            if cost > 0:
                spend_v[vmode] += cost
                spend_v_y[y][vmode] += cost
                spend_all[vmode] += cost
                spend_all_y[y][vmode] += cost
            continue
        if cost > 0:
            cat = INFRA_MODE.get(fam, "other") if m not in ("vehicle_buy", "vehicle_clone") else "other"
            spend_all[cat] += cost
            spend_all_y[y][cat] += cost
        v = a.get("vehicle")
        if m == "vehicle_sell":
            alive.discard(v)
            running.discard(v)
        elif m == "vehicle_attach":
            wagons.add(v)
            running.discard(v)
        elif m == "vehicle_start":
            running.add(v)
        elif m == "vehicle_stop":
            running.discard(v)
        elif m == "order_append" and "tile" in a:
            orders.setdefault(v, []).append(int(a["tile"]))
        elif m == "order_insert" and "tile" in a:
            orders.setdefault(v, []).insert(int(a.get("index", 0)), int(a["tile"]))
        elif m == "order_remove":
            lst = orders.setdefault(v, [])
            i = int(a.get("index", -1))
            if 0 <= i < len(lst):
                lst.pop(i)
        elif m == "order_clear":
            orders[v] = []
        elif m == "order_share" and "other_vehicle" in a:
            orders[v] = orders.setdefault(a["other_vehicle"], [])
        elif m == "company_loan" and "loan" in ret:
            note_loan(game_day(r.get("ticks", t0)), int(ret["loan"]))
        if m in ("vehicle_start", "order_append", "order_insert", "order_share") and v in mode_of \
                and v in running and len(stops(v)) >= 2:
            route_seen.add(mode_of[v])

    # a route counts as seen only if a started vehicle had >= 2 stops at some point; recheck the end state too
    for v in running:
        if v in mode_of and len(stops(v)) >= 2:
            route_seen.add(mode_of[v])
    routes = {mode: set() for mode in MODES}
    for v in alive - wagons:
        if v in mode_of and len(stops(v)) >= 2:
            routes[mode_of[v]].add(frozenset(stops(v)))
    kept = {mode for mode in MODES if routes[mode]}

    def attempt(mode: str) -> dict:
        status = ("not_tried" if not tried[mode] and not bought[mode] else
                  "route_kept" if mode in kept else
                  "route_then_sold" if mode in route_seen else "tried_no_route")
        return {"tried": tried[mode], "vehicles": bought[mode], "spend": spend_all[mode],
                "route": int(mode in route_seen), "kept": int(mode in kept), "status": status}

    total_v = sum(spend_v.values())
    max_loan = max((x[1] for x in loan_path), default=0)
    repaid = None
    if max_loan:
        seen = False
        for day, loan in loan_path:
            seen = seen or loan > 0
            if seen and loan == 0:
                repaid = day
                break
    profit_modes = first_profit[1] if first_profit else Counter()
    end = ("submitted" if submitted else
           "horizon" if horizon_days is not None and last_day >= horizon_days else
           "stopped" if horizon_days is not None else None)
    return {
        "start_date": d0,
        "vehicles_by_mode": bought, "vehicles_total": sum(bought.values()), "rail_wagons_attached": len(wagons),
        "vehicles_by_mode_year": {y: dict(c) for y, c in sorted(bought_y.items(), key=lambda kv: int(kv[0]))},
        "vehicle_spend_by_mode": spend_v, "vehicle_spend_total": total_v,
        "vehicle_spend_by_mode_year": {y: dict(c) for y, c in sorted(spend_v_y.items(), key=lambda kv: int(kv[0]))},
        "spend_by_mode": spend_all, "spend_total": sum(spend_all.values()),
        "spend_by_mode_year": {y: dict(c) for y, c in sorted(spend_all_y.items(), key=lambda kv: int(kv[0]))},
        "non_air_vehicle_spend_share": round(1 - spend_v["air"] / total_v, 4) if total_v else None,
        "first_vehicle_mode": first_vehicle,
        "first_profitable_route_mode": profit_modes.most_common(1)[0][0] if profit_modes else None,
        "first_profitable_modes": sorted(profit_modes),
        "first_profitable_day": first_profit[0] if first_profit else None,
        "routes_by_mode": {mode: len(s) for mode, s in routes.items()},
        "actions": acts, "dry_runs": dry, "failed": failed,
        "failed_rate": round(failed / acts, 4) if acts else None,
        "action_share_by_family": {f: round(n / acts, 4) for f, n in fam_n.most_common()} if acts else {},
        "failed_rate_by_family": {f: round(fam_fail[f] / n, 4) for f, n in fam_n.most_common()},
        "top_errors": dict(errors.most_common(5)),
        "top_errors_by_family": {f: dict(c.most_common(3)) for f, c in sorted(fam_err.items())},
        "rail": attempt("rail"), "water": attempt("water"), "road": attempt("road"), "air": attempt("air"),
        "loan": {"path": loan_path, "max": max_loan, "final": loan_path[-1][1] if loan_path else 0,
                 "first_max_day": next((d for d, x in loan_path if x == max_loan), None) if max_loan else None,
                 "repaid_day": repaid},
        "end": end, "days_played": last_day, "submitted": int(submitted),
    }


# ----------------------------------------------------------------------------------------------- transcript

_FORK_CREATED = re.compile(r'"fork":\s*"[^"]*",\s*"exact"')
_FORK_LEFT = re.compile(r'"fork_days_left":\s*(-?\d+)')


def _tool_outputs(agent_log: Path) -> list[str]:
    """Tool output text of a transcript. Claude stream-json: only ``tool_result`` content (each result also
    appears in ``tool_use_result``, which would count it twice). Other vendors: the raw text."""
    out: list[str] = []
    plain: list[str] = []  # consecutive text lines form one block (Codex prints JSON over several lines)
    for line in Path(agent_log).read_text(encoding="utf-8", errors="replace").splitlines():
        e = None
        if line.startswith('{"'):
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                e = None
        if isinstance(e, dict):
            if plain:
                out.append("\n".join(plain))
                plain = []
            if "session_id" in e and "sessionID" not in e:  # Claude
                if e.get("type") != "user":
                    continue
                for c in (e.get("message") or {}).get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        body = c.get("content")
                        if isinstance(body, list):
                            body = "\n".join(str(b.get("text", "")) for b in body if isinstance(b, dict))
                        out.append(str(body or ""))
                continue
            out.append(json.dumps(e))  # opencode: tool outputs are inside the event
            continue
        plain.append(line)
    if plain:
        out.append("\n".join(plain))
    return out


def transcript_forks(agent_log: Path, max_fork_days: int) -> dict:
    """forks_created and fork_days_used from the transcript (None when there is no transcript)."""
    if not Path(agent_log).exists():
        return {"forks_created": None, "fork_days_used": None, "source": None}
    texts = [t.replace('\\"', '"') for t in _tool_outputs(agent_log)]
    created = sum(len(_FORK_CREATED.findall(t)) for t in texts)
    left = [int(x) for t in texts for x in _FORK_LEFT.findall(t)]
    return {"forks_created": created, "fork_days_used": (max_fork_days - min(left)) if left else 0,
            "source": "agent.log"}


def claude_results(agent_log: Path) -> dict | None:
    """Totals over Claude stream-json ``result`` events (one per session run), or None if there are none."""
    if not Path(agent_log).exists():
        return None
    tot = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0,
           "duration_s": 0.0, "runs": 0}
    for line in Path(agent_log).read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith('{"') or '"type":"result"' not in line.replace(" ", ""):
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") != "result":
            continue
        u = e.get("usage") or {}
        tot["runs"] += 1
        tot["requests"] += int(e.get("num_turns") or 0)
        tot["input_tokens"] += int(u.get("input_tokens") or 0)
        tot["output_tokens"] += int(u.get("output_tokens") or 0)
        tot["cache_read_tokens"] += int(u.get("cache_read_input_tokens") or 0)
        tot["cost_usd"] += float(e.get("total_cost_usd") or 0.0)
        tot["duration_s"] += float(e.get("duration_ms") or 0) / 1000
    return tot if tot["runs"] else None


def otel_usage(otel_dir: Path, player: str, task: str | None = None,
               windows: list[list[float]] | None = None) -> dict | None:
    """LLM requests and tokens of one player from the sink's logs.jsonl: records whose resource has
    ``player=<label>`` (and ``arena.task=<task>`` when given) and whose time falls in one of ``windows``
    ([start, end] epochs; open end allowed). Claude: ``api_request`` events; Codex: ``response.completed``."""
    from railhead.otel import _attr_value

    path = Path(otel_dir) / "logs.jsonl"
    if not path.exists():
        return None
    tot = {"requests": 0, "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cost_usd": 0.0,
           "tool_calls": 0, "first": None, "last": None}

    def inside(t: float) -> bool:
        return not windows or any(w[0] <= t <= (w[1] or float("inf")) for w in windows)

    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if f'"{player}"' not in line:
                continue
            try:
                env = json.loads(line)
            except json.JSONDecodeError:
                continue
            for rl in (env.get("body") or {}).get("resourceLogs", []):
                res = {a["key"]: _attr_value(a["value"]) for a in rl.get("resource", {}).get("attributes", [])}
                if res.get("player") != player or (task and res.get("arena.task") not in (None, task)):
                    continue
                for sl in rl.get("scopeLogs", []):
                    for rec in sl.get("logRecords", []):
                        ns = rec.get("timeUnixNano") or rec.get("observedTimeUnixNano")
                        t = int(ns) / 1e9 if ns and int(ns) > 0 else float(env.get("received") or 0)
                        if not inside(t):
                            continue
                        at = {a["key"]: _attr_value(a["value"]) for a in rec.get("attributes", [])}
                        name = str(at.get("event.name") or "")
                        if name.endswith("api_request") and "cost_usd" in at:
                            tot["requests"] += 1
                            tot["input_tokens"] += int(at.get("input_tokens") or 0)
                            tot["output_tokens"] += int(at.get("output_tokens") or 0)
                            tot["cache_read_tokens"] += int(at.get("cache_read_tokens") or 0)
                            tot["cost_usd"] += float(at.get("cost_usd") or 0.0)
                        elif name == "codex.sse_event" and at.get("event.kind") == "response.completed":
                            tot["requests"] += 1
                            tot["input_tokens"] += int(at.get("input_token_count") or 0)
                            tot["output_tokens"] += int(at.get("output_token_count") or 0)
                            tot["cache_read_tokens"] += int(at.get("cached_token_count") or 0)
                        elif name.endswith("tool_result"):
                            tot["tool_calls"] += 1
                        else:
                            continue
                        tot["first"] = t if tot["first"] is None else min(tot["first"], t)
                        tot["last"] = t if tot["last"] is None else max(tot["last"], t)
    return tot if tot["requests"] or tot["tool_calls"] else None


def model_usage(player_dir: Path, otel_dir: Path | None, player: str, task: str | None,
                windows: list[list[float]] | None = None) -> dict:
    out: dict = {"source": None, "requests": None, "input_tokens": None, "output_tokens": None,
                 "cache_read_tokens": None, "cost_usd": None, "wall_clock_s": None}
    o = otel_usage(otel_dir, player, task, windows) if otel_dir else None
    c = claude_results(Path(player_dir) / "agent.log")
    src = o or c
    if src:
        out.update({k: src[k] for k in ("requests", "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd")})
        out["source"] = "otlp" if o else "agent.log"
    if windows and all(w[1] for w in windows):
        out["wall_clock_s"] = round(sum(w[1] - w[0] for w in windows), 1)
    elif o and o["first"] is not None:
        out["wall_clock_s"] = round(o["last"] - o["first"], 1)
    elif c:
        out["wall_clock_s"] = round(c["duration_s"], 1)
    return out


# ------------------------------------------------------------------------------------------------- one game

def analyse(player_dir: Path, task_dir: Path, otel_dir: Path | None = None, task_id: str | None = None,
            windows: list[list[float]] | None = None, player: str | None = None) -> dict:
    """All behaviour metrics of the game in ``player_dir`` (see DEFINITIONS)."""
    player_dir, task_dir = Path(player_dir), Path(task_dir)
    task = json.loads((task_dir / "task.json").read_text())
    horizon = int(task["horizon_days"])
    max_fork_days = int((task.get("limits") or {}).get("max_fork_days", 365))
    out = analyse_log(player_dir / "state" / "run" / "actions.jsonl", horizon)
    out.update(transcript_forks(player_dir / "agent.log", max_fork_days))
    out["model"] = model_usage(player_dir, otel_dir, player or player_dir.name, task_id or task_dir.name, windows)
    verdict = player_dir / "verify" / "reward.json"
    if verdict.exists():
        v = json.loads(verdict.read_text())
        out["verified"] = {k: v.get(k) for k in ("net_worth", "net_worth_horizon", "reward", "valid", "tamper",
                                                 "rejected", "error")}
    else:
        out["verified"] = None
    event(log, "behaviour analysed", player_dir=str(player_dir), actions=out["actions"], end=out["end"])
    return out


def scalars(b: dict) -> dict[str, float | None]:
    """The numbers the A/B report compares (one value per game; None = not measurable)."""
    s: dict[str, float | None] = {
        "non_air_vehicle_spend_share": b.get("non_air_vehicle_spend_share"),
        "vehicles_total": b.get("vehicles_total"), "vehicle_spend_total": b.get("vehicle_spend_total"),
        "spend_total": b.get("spend_total"),
    }
    vs = b.get("vehicle_spend_total") or 0
    for mode in MODES:
        s[f"vehicles_{mode}"] = (b.get("vehicles_by_mode") or {}).get(mode)
        s[f"vehicle_spend_share_{mode}"] = round((b.get("vehicle_spend_by_mode") or {}).get(mode, 0) / vs, 4) \
            if vs else None
        s[f"routes_{mode}"] = (b.get("routes_by_mode") or {}).get(mode)
    for mode in ("rail", "water"):
        att = b.get(mode) or {}
        s[f"{mode}_tried"] = int(bool(att.get("tried")))
        s[f"{mode}_route"] = att.get("route")
        s[f"{mode}_kept"] = att.get("kept")
    s.update({"actions": b.get("actions"), "failed_rate": b.get("failed_rate"),
              "forks_created": b.get("forks_created"), "fork_days_used": b.get("fork_days_used"),
              "max_loan": (b.get("loan") or {}).get("max"), "final_loan": (b.get("loan") or {}).get("final"),
              "submitted": b.get("submitted"), "days_played": b.get("days_played")})
    m = b.get("model") or {}
    for k in ("requests", "input_tokens", "output_tokens", "cost_usd", "wall_clock_s"):
        s[f"model_{k}"] = m.get(k)
    return s


SCALAR_DEFINITIONS = {
    "non_air_vehicle_spend_share": DEFINITIONS["non_air_vehicle_spend_share"],
    "vehicles_total": "vehicles bought or cloned, all modes",
    "vehicle_spend_total": "money paid for vehicles, all modes",
    "spend_total": "vehicle plus construction spend, all modes and 'other'",
    **{f"vehicles_{m}": f"vehicles bought or cloned, {m}" for m in MODES},
    **{f"vehicle_spend_share_{m}": f"{m} vehicle spend / all vehicle spend" for m in MODES},
    **{f"routes_{m}": f"distinct {m} stop-tile sets of vehicles alive at the end" for m in MODES},
    **{f"{m}_tried": f"1 if any {m} action or {m} vehicle buy was attempted" for m in ("rail", "water")},
    **{f"{m}_route": f"1 if a started {m} vehicle ever had >= 2 distinct order tiles" for m in ("rail", "water")},
    **{f"{m}_kept": f"1 if such a {m} vehicle is alive at the end" for m in ("rail", "water")},
    "actions": DEFINITIONS["actions"], "failed_rate": DEFINITIONS["failed_rate"],
    "forks_created": DEFINITIONS["forks_created"], "fork_days_used": DEFINITIONS["fork_days_used"],
    "max_loan": "highest loan in the loan path", "final_loan": "loan at the end of the log",
    "submitted": "1 if the player ran rh submit", "days_played": DEFINITIONS["days_played"],
    "model_requests": "LLM requests (see 'model')", "model_input_tokens": "input tokens",
    "model_output_tokens": "output tokens", "model_cost_usd": "cost reported by the harness (API price)",
    "model_wall_clock_s": "wall-clock seconds of the cell",
}


def main(argv: list[str]) -> int:
    if not argv or "--task" not in argv:
        print("usage: railhead ab behaviour <player_dir> --task <task_dir> [--otel DIR] [--json]")
        return 2
    otel = Path(argv[argv.index("--otel") + 1]) if "--otel" in argv else None
    b = analyse(Path(argv[0]), Path(argv[argv.index("--task") + 1]), otel)
    print(json.dumps(b if "--json" in argv else {"scalars": scalars(b), "first_vehicle_mode": b["first_vehicle_mode"],
                                                  "first_profitable_route_mode": b["first_profitable_route_mode"],
                                                  "end": b["end"], "top_errors": b["top_errors"],
                                                  "rail": b["rail"], "water": b["water"]}, indent=1))
    return 0
