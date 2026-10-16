"""Export catalog tasks as Harbor task directories (run *inside* the Railhead image so the sealed
hashes are of the exact files the verifier will trust).

    railhead export <out_dir> [task_id ...] [--image ghcr.io/rachittshah/railhead:0.1.0]
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from railhead.seal import seal_task, write_sha256_manifest
from railhead.task import TaskSpec, tasks_root

ORG = "rachittshah"
DEFAULT_IMAGE = "ghcr.io/rachittshah/railhead:0.1.0"

HOW_TO_PLAY = """\
## How to play

You control one transport company in a running OpenTTD world. Everything goes through the `rh`
command (already on PATH). The world is **frozen** except when you `rh wait`, so think as long as you
like. Tiles are written `x,y`.

```bash
rh status                          # dashboard: date, money, objective progress, alerts
rh brief                           # this task's objective, horizon and budgets (JSON)
rh help                            # every command
rh industries | rh towns           # where cargo is produced / accepted, where people live
rh map x=0 y=0 w=40 h=24           # ASCII map of a region (legend included)
rh tile tile=12,40                 # one tile in detail, incl. cargo acceptance around it
rh engines vtype=road              # vehicles you can buy, with capacity / speed / cost
rh road line from_tile=10,4 to_tile=30,4     # build like a human player: drag a road
rh road depot tile=12,5 front_tile=12,4
rh road stop tile=20,4 front_tile=21,4 kind=truck
rh vehicle buy depot_tile=12,5 engine=116 cargo=1
rh order append vehicle=3 tile=20,4 flags='["full_load"]'
rh vehicle start vehicle=3
rh wait days=30 until=vehicle_crashed,cash_below:10000   # time passes only here
rh fork create name=try1           # a sandbox copy of the world (shows *a* possible future)
rh fork do name=try1 m=road_line from_tile=10,4 to_tile=30,4
rh fork wait name=try1 days=90     # costs imagination budget (fork-days)
rh fork drop name=try1
rh submit                          # optional: you are done; the world keeps running without you
```
Any build command accepts `dry_run=true` to get its cost without doing it. Errors name a likely fix.

## Rules of the game

* Your reward is computed by **replaying your own action log** from the sealed start state in a clean
  engine, then letting the world run on without you. Anything done outside `rh` does not count.
* Only player actions exist. There are no cheats, and the world does not wait for you past the horizon.
"""


def instruction_md(spec: TaskSpec) -> str:
    lines = [f"# {spec.title or spec.task_id}", "", spec.briefing.strip(), "",
             f"* Horizon: **{spec.horizon_days} game-days** from now. After that (or after `rh submit`) the "
             f"world runs **{spec.runoff_days} more days** without you before scoring.",
             f"* Imagination budget: **{spec.max_fork_days} fork-days**, at most **{spec.max_forks}** live forks.",
             "", HOW_TO_PLAY]
    return "\n".join(lines)


def task_toml(spec: TaskSpec, image: str, version: str) -> str:
    kw = ["openttd", "reinforcement-learning", "simulation", "long-horizon", spec.family]
    return f'''schema_version = "1.4"

[task]
name = "{ORG}/railhead-{spec.task_id}"
version = "{version}"
description = "Railhead {spec.family}: {spec.title}"
keywords = {json.dumps(kw)}

[[task.authors]]
name = "Rachitt Shah"

[metadata]
family = "{spec.family}"
split = "{spec.split}"
seed = {int(spec.scenario.get("seed", 0))}
difficulty = "{spec.scenario.get("difficulty", "medium")}"
category = "games-simulation"
tags = {json.dumps(kw)}
horizon_days = {spec.horizon_days}
runoff_days = {spec.runoff_days}

[agent]
timeout_sec = {int(spec.scenario.get("agent_timeout_sec", 2400))}

[verifier]
timeout_sec = 900

[environment]
docker_image = "{image}"
cpus = 1
memory_mb = 2048
build_timeout_sec = 900

[environment.env]
RAILHEAD_TASK = "{spec.task_id}"

[environment.healthcheck]
command = "/usr/local/bin/railhead-up {spec.task_id}"
timeout_sec = 180
interval_sec = 5
start_period_sec = 0
retries = 3
'''


TEST_SH = """#!/bin/bash
# Proof-of-play verifier. Never trusts the live game: the reward is recomputed by replaying the
# agent's action log from the sealed start state in a fresh engine.
set -u
OUT=/logs/verifier
mkdir -p "$OUT"
fail() { echo "{\\"reward\\": 0.0, \\"valid\\": 1, \\"tamper\\": $1, \\"rejected\\": 0}" > "$OUT/reward.json"; echo 0 > "$OUT/reward.txt"; exit 0; }
# 1) integrity of every trusted file, checked with coreutils before any Python runs
sha256sum -c /tests/sealed.sha256 --quiet > "$OUT/integrity.txt" 2>&1 || fail 1
# 2) stop the live world (flushes the log); keep a copy of the proof
/opt/railhead/venv/bin/railhead down > /dev/null 2>&1 || true
LOG=/var/lib/railhead/run/actions.jsonl
if [ ! -s "$LOG" ]; then LOG=/tmp/empty-actions.jsonl; echo '{"kind":"header","v":1,"ticks":0}' > "$LOG"; fi
cp "$LOG" "$OUT/actions.jsonl"
# 3) replay + run-off + score
/opt/railhead/venv/bin/python -I -m railhead.verify /tests "/opt/railhead/tasks/__TASK__/start.sav" "$LOG" "$OUT" ${RAILHEAD_CREDIT:+--credit}
"""

SOLVE_SH = """#!/bin/bash
# Oracle: re-issue a known-good action log through the normal player interface (`rh` daemon).
set -e
/usr/local/bin/railhead-up __TASK__
/opt/railhead/venv/bin/railhead play /solution/oracle.jsonl
"""


def export_task(task_id: str, out_dir: Path, image: str = DEFAULT_IMAGE, version: str = "0.1.0") -> Path:
    src = tasks_root() / task_id
    spec = TaskSpec.load(src)
    dst = Path(out_dir) / spec.task_id
    if dst.exists():
        shutil.rmtree(dst)
    (dst / "environment").mkdir(parents=True)
    (dst / "tests").mkdir()
    (dst / "solution").mkdir()
    (dst / "task.toml").write_text(task_toml(spec, image, version))
    (dst / "instruction.md").write_text(instruction_md(spec))
    (dst / "environment" / "Dockerfile").write_text(
        f"FROM {image}\nENV RAILHEAD_TASK={spec.task_id}\n")
    seal_task(src)
    shutil.copy(src / "task.json", dst / "tests" / "task.json")
    shutil.copy(src / "sealed.json", dst / "tests" / "sealed.json")
    if (src / "secret.json").exists():
        shutil.copy(src / "secret.json", dst / "tests" / "secret.json")
    write_sha256_manifest(dst / "tests" / "sealed.sha256")
    (dst / "tests" / "test.sh").write_text(TEST_SH.replace("__TASK__", spec.task_id))
    if (src / "oracle.jsonl").exists():
        shutil.copy(src / "oracle.jsonl", dst / "solution" / "oracle.jsonl")
    (dst / "solution" / "solve.sh").write_text(SOLVE_SH.replace("__TASK__", spec.task_id))
    for p in (dst / "tests" / "test.sh", dst / "solution" / "solve.sh"):
        p.chmod(0o755)
    return dst


def export_all(out_dir: Path, task_ids: list[str] | None = None, image: str = DEFAULT_IMAGE,
               version: str = "0.1.0") -> list[Path]:
    ids = task_ids or sorted(p.name for p in tasks_root().iterdir() if (p / "task.json").exists())
    return [export_task(t, out_dir, image, version) for t in ids]
