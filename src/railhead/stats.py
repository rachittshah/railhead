"""Multi-map statistics: several arena exports (one map each) -> per-player means and paired intervals.

    railhead stats <export.json | maps.json | map_dir>... [--tasks DIR] [--players a,b,...]
                   [--finished-only] [--boot N] [--seed S] [--out FILE] [--json]
    railhead stats collect <map_dir>[=<task_id>]... --tasks DIR --out FILE [--players a,b,...]

The unit of evidence is the MAP. Several games of one player on one map are averaged into one cell first.
Paired comparisons use only the maps that both players played; the bootstrap resamples those maps with
replacement (the same draw for both players) and gives a 95 % percentile interval of the mean difference.
A pair is "tied" when the interval contains zero. The sign test counts maps won, without zero differences.
Ranking: pairwise wins minus losses on asinh(net worth / 100k) (only separated pairs count), then mean asinh.

`collect` turns map directories (<map_dir>/<player>/state/run/actions.jsonl + verify/reward.json) into one
document {"maps": [<arena export + end state>, ...]}. A rejected, tampered or failed verification stays in
the document with its status and is left out of the statistics with a reason.
"""

from __future__ import annotations

import json
import math
import statistics
import sys
from itertools import combinations
from pathlib import Path

from railhead.actionlog import read_log

SCALE = 100_000.0  # asinh(net worth / 100k): log-like for large values, defined for negative values
FINISHED = ("submitted", "horizon")


# ----------------------------------------------------------------------------------------------- collection

def end_state(log: Path, horizon_days: int) -> dict:
    """How the game ended, from the action log alone: submitted | horizon (no submit) | stopped (mid-game)."""
    start = last = None
    submitted = False
    for r in read_log(log):
        ck = r.raw.get("checksum")
        if r.kind == "header" and isinstance(ck, dict):
            start = int(ck["date"])
        elif isinstance(ck, dict) and "date" in ck:
            last = int(ck["date"])
        if r.kind == "meta" and r.raw.get("m") == "submit":
            submitted = True
    played = (last - start) if (start is not None and last is not None) else 0
    end = "submitted" if submitted else "horizon" if played >= horizon_days else "stopped"
    return {"end": end, "days_played": played, "submitted": int(submitted)}


def collect_map(map_dir: Path, task_dir: Path, players: list[str] | None = None) -> dict:
    """One arena export (docs/data row shape) plus the end state of each game and the task's certificates."""
    from railhead import arena, rewards

    task = json.loads((Path(task_dir) / "task.json").read_text())
    obj, horizon = task["objective"], int(task["horizon_days"])
    w_idle, w_base = obj["w_idle"], obj["w_base"]
    rows, curves, mix = [], {}, {}
    for name, pts in arena.curves(Path(map_dir)).items():
        if players and name not in players:
            continue
        log = Path(map_dir) / name / "state" / "run" / "actions.jsonl"
        last, verdict = pts[-1], Path(map_dir) / name / "verify" / "reward.json"
        end = end_state(log, horizon)
        base = {"player": name, "vehicles": last.get("vehicles"), "stations": last.get("stations"),
                "reached": last.get("date"), **end}
        if not verdict.exists():
            rows.append({**base, "status": "playing", "net_worth_live": last.get("net_worth")})
        else:
            v = json.loads(verdict.read_text())
            bad = next((k for k in ("tamper", "rejected", "error") if v.get(k)), None)
            if bad or "net_worth" not in v:
                rows.append({**base, "status": bad or "error", "valid": v.get("valid", 0)})
            else:
                rows.append({**base, "status": "verified", "net_worth": v["net_worth"],
                             "net_worth_horizon": v["net_worth_horizon"],
                             "gain_x_baseline": round((v["net_worth"] - w_idle) / (w_base - w_idle), 1),
                             "score_k50": round(rewards.tycoon_log(v["net_worth"], w_idle, w_base, 50), 3),
                             "score_k1000": round(rewards.tycoon_log(v["net_worth"], w_idle, w_base, 1000), 3),
                             "actions": v["acts"], "failed_actions": v["failed_acts"], "waits": v["waits"],
                             "divergences": v["divergences"], "valid": v["valid"]})
        curves[name] = [{k: s.get(k) for k in ("date", "net_worth", "money", "loan", "vehicles", "stations", "acts")}
                        for s in pts if s.get("date")]
        mix[name] = arena.fleet_mix(log)
    rows += [{"player": "baseline", "status": "certificate", "net_worth": w_base, "gain_x_baseline": 1.0},
             {"player": "idle", "status": "certificate", "net_worth": w_idle, "gain_x_baseline": 0.0}]
    scored = sorted((r for r in rows if "net_worth" in r), key=lambda r: (-r["net_worth"], r["status"] == "certificate"))
    rest = [r for r in rows if "net_worth" not in r]
    return {"task": Path(task_dir).name, "objective": {"w_idle": w_idle, "w_base": w_base},
            "horizon_days": horizon, "runoff_days": task.get("runoff_days"),
            "results": scored + rest, "curves": curves, "fleet_mix": mix}


def load_docs(paths: list[Path], tasks_dir: Path | None = None, players: list[str] | None = None) -> list[dict]:
    """Map documents from export files ({"task", "results"}), multi-map files ({"maps": [...]}) or map dirs."""
    docs: list[dict] = []
    for p in paths:
        p = Path(p)
        if p.is_dir():
            if tasks_dir is None:
                raise SystemExit(f"{p} is a directory: pass --tasks DIR to find its task")
            docs.append(collect_map(p, Path(tasks_dir) / p.name, players))
            continue
        d = json.loads(p.read_text())
        docs += d.get("maps", [d])
    return docs


# ---------------------------------------------------------------------------------------------- statistics

def _metrics(r: dict) -> dict:
    return {"net_worth": float(r["net_worth"]), "asinh": math.asinh(float(r["net_worth"]) / SCALE),
            "score_k50": r.get("score_k50"), "score_k1000": r.get("score_k1000")}


def _exclusion(r: dict, finished_only: bool) -> str | None:
    if r.get("status") != "verified":
        return r.get("status") if r.get("status") in ("rejected", "tamper", "error") else f"not verified ({r.get('status')})"
    if r.get("valid", 1) != 1:
        return "valid=0"
    if finished_only and r.get("end") not in FINISHED:
        return f"end={r.get('end', 'unknown')}"
    return None


def cells(docs: list[dict], players: list[str] | None = None, finished_only: bool = False,
          excluded: list | None = None) -> dict[str, dict[str, dict]]:
    """map -> player -> mean of each metric over that player's scored games on that map (+ "games")."""
    games: dict[str, dict[str, list[dict]]] = {}  # pooled over documents: two exports may cover one map
    for d in docs:
        for r in d["results"]:
            if r.get("status") == "certificate" or (players and r["player"] not in players):
                continue
            why = _exclusion(r, finished_only)
            if why:
                if excluded is not None:
                    excluded.append({"map": d["task"], "player": r["player"], "reason": why})
                continue
            games.setdefault(d["task"], {}).setdefault(r["player"], []).append(_metrics(r))
    out: dict[str, dict[str, dict]] = {}
    for m, row in games.items():
        for p, gs in row.items():
            cell = {"games": len(gs)}
            for k in ("net_worth", "asinh", "score_k50", "score_k1000"):
                vals = [g[k] for g in gs if g[k] is not None]
                cell[k] = statistics.fmean(vals) if vals else None
            out.setdefault(m, {})[p] = cell
    return out


def sign_test(diffs: list[float]) -> tuple[float | None, int]:
    """Two-sided exact sign test; zero differences are dropped. Returns (p, number of non-zero differences)."""
    nz = [x for x in diffs if x != 0]
    n = len(nz)
    if n == 0:
        return (None, 0) if not diffs else (1.0, 0)
    k = min(sum(x > 0 for x in nz), sum(x < 0 for x in nz))
    p = 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, p), n


def bootstrap(diffs: list[float], boot: int, rng) -> tuple[float, float]:
    """95 % percentile interval of the mean of `diffs`, resampling its entries (maps) with replacement."""
    import numpy as np

    d = np.asarray(diffs, dtype=float)
    idx = rng.integers(0, len(d), size=(boot, len(d)))
    means = d[idx].mean(axis=1)
    lo, hi = np.percentile(means, [2.5, 97.5])
    return float(lo), float(hi)


def _pair(name_a: str, name_b: str, table: dict, boot: int, rng) -> dict:
    shared = sorted(m for m, row in table.items() if name_a in row and name_b in row)
    out = {"a": name_a, "b": name_b, "n": len(shared), "maps": shared}
    if not shared:
        return out | {"net_worth": None, "asinh": None, "sign_p": None, "n_nonzero": 0, "wins_a": 0, "wins_b": 0}
    if statistics.fmean(table[m][name_a]["asinh"] - table[m][name_b]["asinh"] for m in shared) < 0:
        name_a, name_b = name_b, name_a  # orient every pair so that `a` is ahead on the asinh mean
        out |= {"a": name_a, "b": name_b}
    for metric in ("net_worth", "asinh"):
        diffs = [table[m][name_a][metric] - table[m][name_b][metric] for m in shared]
        lo, hi = bootstrap(diffs, boot, rng)
        out[metric] = {"mean": statistics.fmean(diffs), "median": statistics.median(diffs), "lo": lo, "hi": hi,
                       "tied": bool(lo <= 0 <= hi)}
    diffs = [table[m][name_a]["net_worth"] - table[m][name_b]["net_worth"] for m in shared]
    p, nz = sign_test(diffs)
    return out | {"sign_p": p, "n_nonzero": nz, "wins_a": sum(x > 0 for x in diffs), "wins_b": sum(x < 0 for x in diffs)}


def _summary(player: str, table: dict) -> dict:
    maps = sorted(m for m, row in table.items() if player in row)
    cs = [table[m][player] for m in maps]

    def mean(k):
        vals = [c[k] for c in cs if c[k] is not None]
        return statistics.fmean(vals) if vals else None

    nws = [c["net_worth"] for c in cs]
    return {"player": player, "n_maps": len(maps), "n_games": sum(c["games"] for c in cs), "maps": maps,
            "mean_net_worth": statistics.fmean(nws), "median_net_worth": statistics.median(nws),
            "mean_asinh": mean("asinh"), "mean_score_k50": mean("score_k50"), "mean_score_k1000": mean("score_k1000")}


def fleet_shares(docs: list[dict], table: dict) -> list[dict]:
    """Vehicles bought or cloned by mode per scored game, the air share, and whether it is the map's winner."""
    rows = []
    for d in docs:
        row = table.get(d["task"], {})
        best = max(row, key=lambda p: row[p]["net_worth"]) if row else None
        for p, mix in sorted((d.get("fleet_mix") or {}).items()):
            if p not in row:
                continue
            total = sum(mix.values())
            end = next((r.get("end") for r in d["results"] if r["player"] == p), None)
            rows.append({"map": d["task"], "player": p, **mix, "total": total,
                         "air_share": round(mix.get("air", 0) / total, 3) if total else None,
                         "winner": p == best, "end": end})
    return rows


def aggregate(docs: list[dict], boot: int = 10_000, seed: int = 0, players: list[str] | None = None,
              finished_only: bool = False) -> dict:
    import numpy as np

    excluded: list[dict] = []
    table = cells(docs, players, finished_only, excluded)
    names = sorted({p for row in table.values() for p in row})
    rng = np.random.default_rng(seed)
    pairs = [_pair(a, b, table, boot, rng) for a, b in combinations(names, 2)]
    summ = [_summary(p, table) for p in names]
    by = {s["player"]: s for s in summ}
    score = dict.fromkeys(names, 0)
    tied: dict[str, list[str]] = {p: [] for p in names}
    for pr in pairs:
        if pr["n"] == 0:
            continue
        if pr["asinh"]["tied"]:
            tied[pr["a"]].append(pr["b"])
            tied[pr["b"]].append(pr["a"])
        else:
            score[pr["a"]] += 1
            score[pr["b"]] -= 1
    order = sorted(names, key=lambda p: (-score[p], -(by[p]["mean_asinh"] or 0.0)))
    ranking = [{"rank": i + 1, "player": p, "separated_wins_minus_losses": score[p], "mean_asinh": by[p]["mean_asinh"],
                "tied_with": sorted(tied[p], key=order.index),
                "not_compared": sorted(q for q in names if q != p and not any(
                    pr["n"] and {pr["a"], pr["b"]} == {p, q} for pr in pairs))}
               for i, p in enumerate(order)]
    return {"maps": sorted(table), "boot": boot, "seed": seed, "finished_only": finished_only,
            "unit": "map (several games of one player on one map are averaged first)",
            "rank_basis": "separated pairwise wins minus losses on asinh(net_worth/100k), then mean asinh",
            "cells": table, "players": sorted(summ, key=lambda s: order.index(s["player"])), "pairs": pairs,
            "ranking": ranking, "excluded": excluded, "fleet": fleet_shares(docs, table)}


# -------------------------------------------------------------------------------------------------- report

def _m(x: float | None) -> str:
    return "-" if x is None else f"{x / 1e6:,.2f}M"


def report(agg: dict, docs: list[dict]) -> str:
    names = [s["player"] for s in agg["players"]]
    ends = {(d["task"], r["player"]): r.get("end") for d in docs for r in d["results"]}
    tag = {"submitted": "", "horizon": " h", "stopped": " s", None: " ?"}
    lines = ["net worth after run-off, by map (h = horizon without submit, s = stopped mid-game, ? = unknown)",
             "map".ljust(18) + "".join(n[:16].rjust(18) for n in names)]
    for m in agg["maps"]:
        row = agg["cells"][m]
        lines.append(m.ljust(18) + "".join(
            (_m(row[n]["net_worth"]) + tag.get(ends.get((m, n)), " ?")).rjust(18) if n in row else "-".rjust(18)
            for n in names))
    lines += ["", "per player (means over map cells)",
              f"{'player':<18}{'maps':>5}{'games':>6}{'mean NW':>12}{'median NW':>12}{'asinh':>8}{'k50':>7}{'k1000':>7}"]
    for s in agg["players"]:
        k50 = "-" if s["mean_score_k50"] is None else f"{s['mean_score_k50']:.3f}"
        k1k = "-" if s["mean_score_k1000"] is None else f"{s['mean_score_k1000']:.3f}"
        lines.append(f"{s['player']:<18}{s['n_maps']:>5}{s['n_games']:>6}{_m(s['mean_net_worth']):>12}"
                     f"{_m(s['median_net_worth']):>12}{s['mean_asinh']:>8.3f}{k50:>7}{k1k:>7}")
    lines += ["", f"paired differences a - b on shared maps (95% bootstrap over maps, B={agg['boot']})"]
    for p in agg["pairs"]:
        if not p["n"]:
            lines.append(f"{p['a']} vs {p['b']}: no shared map")
            continue
        nw, ah = p["net_worth"], p["asinh"]
        lines.append(f"{p['a']} - {p['b']}  n={p['n']}  NW {_m(nw['mean'])} [{_m(nw['lo'])}, {_m(nw['hi'])}]"
                     f"{' TIED' if nw['tied'] else ''}  asinh {ah['mean']:.2f} [{ah['lo']:.2f}, {ah['hi']:.2f}]"
                     f"{' TIED' if ah['tied'] else ''}  maps won {p['wins_a']}-{p['wins_b']} sign p={p['sign_p']:.3f}")
    lines += ["", "ranking (" + agg["rank_basis"] + ")"]
    for r in agg["ranking"]:
        extra = f"  tied with: {', '.join(r['tied_with'])}" if r["tied_with"] else ""
        extra += f"  not compared: {', '.join(r['not_compared'])}" if r["not_compared"] else ""
        lines.append(f"{r['rank']}. {r['player']}{extra}")
    if agg["fleet"]:
        lines += ["", "vehicles bought or cloned by mode (rail includes wagons); * = map winner"]
        for f in agg["fleet"]:
            share = "-" if f["air_share"] is None else f"{f['air_share']:.0%}"
            lines.append(f"{f['map']:<18}{f['player'] + ('*' if f['winner'] else ''):<20} road {f['road']:>4} "
                         f"rail {f['rail']:>4} air {f['air']:>4} water {f['water']:>3}  air share {share:>5}"
                         f"  end={f['end']}")
    if agg["excluded"]:
        lines += ["", "excluded: " + "; ".join(f"{x['map']}/{x['player']}: {x['reason']}" for x in agg["excluded"])]
    return "\n".join(lines)


def _opt(argv: list[str], flag: str, default=None):
    return argv[argv.index(flag) + 1] if flag in argv else default


def _positional(argv: list[str]) -> list[str]:
    takes = {"--tasks", "--players", "--boot", "--seed", "--out"}
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
        elif a in takes:
            skip = True
        elif not a.startswith("--"):
            out.append(a)
    return out


USAGE = ("usage: railhead stats <export.json|maps.json|map_dir>... [--tasks DIR] [--players a,b] [--finished-only]"
         " [--boot N] [--seed S] [--out FILE] [--json]\n"
         "       railhead stats collect <map_dir>[=<task_id>]... --tasks DIR --out FILE [--players a,b]")


def main(argv: list[str]) -> int:
    pos = _positional(argv)
    players = _opt(argv, "--players")
    players = players.split(",") if players else None
    if not pos or (pos[0] == "collect" and (len(pos) < 2 or "--tasks" not in argv or "--out" not in argv)):
        print(USAGE, file=sys.stderr)
        return 2
    if pos[0] == "collect":
        tasks = Path(_opt(argv, "--tasks"))
        maps = []
        for spec in pos[1:]:
            d, _, tid = spec.partition("=")
            maps.append(collect_map(Path(d), tasks / (tid or Path(d).name), players))
        out = Path(_opt(argv, "--out"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"maps": maps}, indent=1) + "\n")
        print(json.dumps({"collected": len(maps), "out": str(out)}))
        return 0
    tasks = _opt(argv, "--tasks")
    docs = load_docs([Path(p) for p in pos], Path(tasks) if tasks else None, players)
    agg = aggregate(docs, boot=int(_opt(argv, "--boot", 10_000)), seed=int(_opt(argv, "--seed", 0)),
                    players=players, finished_only="--finished-only" in argv)
    if "--out" in argv:
        out = Path(_opt(argv, "--out"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(agg, indent=1) + "\n")
    print(json.dumps(agg) if "--json" in argv else report(agg, docs))
    return 0
