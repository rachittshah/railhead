"""Arena scoreboard: growth curves of several players on the same task, from their action logs.

    railhead arena <arena_dir>            # arena_dir/<player>/state/run/actions.jsonl
    railhead arena <arena_dir> --json
    railhead arena <arena_dir> --export out.json --task tasks/<task_id> [--rename a=b,...]   # for docs/data
"""

from __future__ import annotations

import json
from pathlib import Path

from railhead.actionlog import read_log

# Engine IDs of the original vehicle set (OpenTTD src/table/engines.h): 116 rail, 88 road, 11 ship,
# 41 aircraft, in that order. Railhead tasks load no NewGRF, so these ranges hold for every climate.
ENGINE_MODES = ((116, "rail"), (204, "road"), (215, "water"), (256, "air"))


def engine_mode(engine: int) -> str:
    return next(mode for end, mode in ENGINE_MODES if engine < end)


def fleet_mix(log: Path) -> dict[str, int]:
    """Vehicles bought or cloned (successful, not dry runs), by transport mode. Rail includes wagons."""
    mix, mode_of = {"road": 0, "rail": 0, "air": 0, "water": 0}, {}
    for r in read_log(log):
        raw = r.raw
        if r.kind != "act" or not raw.get("ok") or "vehicle" not in (raw.get("ret") or {}):
            continue
        if raw.get("m") == "vehicle_buy":
            mode = engine_mode(int(raw["ret"].get("engine", raw["a"]["engine"])))
        elif raw.get("m") == "vehicle_clone":
            mode = mode_of.get(raw["a"].get("vehicle"))
        else:
            continue
        if mode:
            mode_of[raw["ret"]["vehicle"]] = mode
            mix[mode] += 1
    return mix


def curves(arena_dir: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for p in sorted(Path(arena_dir).iterdir()):
        log = p / "state" / "run" / "actions.jsonl"
        if not log.exists():
            continue
        pts, acts, failed = [], 0, 0
        for r in read_log(log):
            if r.kind == "act":
                acts += 1
                failed += 0 if r.raw.get("ok") else 1
            elif r.kind == "wait" and r.raw.get("snap"):
                pts.append(dict(r.raw["snap"], acts=acts, failed=failed))
        out[p.name] = pts or [{"acts": acts, "failed": failed}]
    return out


def results(arena_dir: Path, task_dir: Path) -> list[dict]:
    """One row per player: the proof-of-play verdict if there is one, else the live state from the log.
    The certified baselines of the task (scripted route, no play) are rows too."""
    from railhead import rewards

    obj = json.loads((Path(task_dir) / "task.json").read_text())["objective"]
    w_idle, w_base = obj["w_idle"], obj["w_base"]
    done, live = [], []
    for name, pts in curves(arena_dir).items():
        last, verdict = pts[-1], Path(arena_dir) / name / "verify" / "reward.json"
        if verdict.exists():
            v = json.loads(verdict.read_text())
            done.append({"player": name, "status": "verified", "net_worth": v["net_worth"],
                         "net_worth_horizon": v["net_worth_horizon"],
                         "gain_x_baseline": round((v["net_worth"] - w_idle) / (w_base - w_idle), 1),
                         "score_k50": round(rewards.tycoon_log(v["net_worth"], w_idle, w_base, 50), 3),
                         "score_k1000": round(rewards.tycoon_log(v["net_worth"], w_idle, w_base, 1000), 3),
                         "actions": v["acts"], "failed_actions": v["failed_acts"], "waits": v["waits"],
                         "divergences": v["divergences"], "valid": v["valid"], "vehicles": last.get("vehicles"),
                         "stations": last.get("stations"), "reached": last.get("date")})
        else:
            live.append({"player": name, "status": "playing", "net_worth_live": last.get("net_worth"),
                         "reached": last.get("date"), "vehicles": last.get("vehicles"), "stations": last.get("stations")})
    done += [{"player": "baseline", "status": "certificate", "net_worth": w_base, "gain_x_baseline": 1.0},
             {"player": "idle", "status": "certificate", "net_worth": w_idle, "gain_x_baseline": 0.0}]
    done.sort(key=lambda r: (-r["net_worth"], r["status"] == "certificate"))
    return done + live


def table(arena_dir: Path) -> str:
    rows = ["player   date         net worth      money       loan  value     veh  stn  acts fail"]
    for name, pts in curves(arena_dir).items():
        s = pts[-1]
        rows.append(f"{name:<8} {s.get('date', '-'):<10} {s.get('net_worth', 0):>12,} {s.get('money', 0):>10,} "
                    f"{s.get('loan', 0):>9,} {s.get('value', 0) or 0:>9,} {s.get('vehicles', 0):>4} "
                    f"{s.get('stations', 0):>4} {s.get('acts', 0):>5} {s.get('failed', 0):>4}")
    return "\n".join(rows)


def main(argv: list[str]) -> int:
    d = Path(argv[0])
    if "--export" in argv:
        task = Path(argv[argv.index("--task") + 1])
        out = Path(argv[argv.index("--export") + 1])
        cv = {p: [{k: s.get(k) for k in ("date", "net_worth", "money", "loan", "vehicles", "stations", "acts")}
                  for s in pts if s.get("date")] for p, pts in curves(d).items()}
        out.parent.mkdir(parents=True, exist_ok=True)
        mix = {p.name: fleet_mix(p / "state/run/actions.jsonl") for p in sorted(d.iterdir())
               if (p / "state/run/actions.jsonl").exists()}
        doc = {"task": task.name, "results": results(d, task), "curves": cv, "fleet_mix": mix}
        if "--rename" in argv:  # e.g. --rename opus=claude-opus-5-5,haiku=claude-haiku-5-5
            names = dict(x.split("=", 1) for x in argv[argv.index("--rename") + 1].split(","))
            for r in doc["results"]:
                r["player"] = names.get(r["player"], r["player"])
            doc["curves"] = {names.get(k, k): v for k, v in cv.items()}
            doc["fleet_mix"] = {names.get(k, k): v for k, v in mix.items()}
        out.write_text(json.dumps(doc, indent=1) + "\n")
        print(json.dumps({"export": str(out)}))
        return 0
    print(json.dumps(curves(d)) if "--json" in argv else table(d))
    return 0
