"""Arena scoreboard: growth curves of several players on the same task, from their action logs.

    railhead arena <arena_dir>            # arena_dir/<player>/state/run/actions.jsonl
    railhead arena <arena_dir> --json
"""

from __future__ import annotations

import json
from pathlib import Path

from railhead.actionlog import read_log


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
    print(json.dumps(curves(d)) if "--json" in argv else table(d))
    return 0
