"""Build and publish the Hugging Face dataset (rachittshah/railhead).

configs:
  tasks         one row per task: metadata, certificates, sealed start state, full Harbor task tarball
  trajectories  one row per oracle trajectory: action log (proof of play), reward components,
                leave-one-turn-out counterfactual credit

    railhead hub build <tasks_dir> <harbor_dir> <out_dir> [--credit]
    railhead hub push <out_dir> [--repo rachittshah/railhead]
"""

from __future__ import annotations

import io
import json
import tarfile
import tempfile
from pathlib import Path

from railhead.harbor_export import DEFAULT_IMAGE, ORG
from railhead.task import TaskSpec


def _tar_dir(path: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        for p in sorted(path.rglob("*")):
            ti = tf.gettarinfo(str(p), arcname=str(p.relative_to(path.parent)))
            ti.mtime, ti.uid, ti.gid, ti.uname, ti.gname = 0, 0, 0, "", ""
            if p.is_file():
                with open(p, "rb") as fh:
                    tf.addfile(ti, fh)
            else:
                tf.addfile(ti)
    return buf.getvalue()


def task_rows(tasks_dir: Path, harbor_dir: Path, image: str = DEFAULT_IMAGE) -> list[dict]:
    rows = []
    for tdir in sorted(p for p in Path(tasks_dir).iterdir() if (p / "task.json").exists()):
        spec = TaskSpec.load(tdir)
        certs = json.loads((tdir / "certificates.json").read_text()) if (tdir / "certificates.json").exists() else {}
        hdir = Path(harbor_dir) / spec.task_id
        rows.append({
            "task_id": spec.task_id,
            "family": spec.family,
            "variant": spec.scenario.get("variant", spec.family),
            "split": spec.split,
            "seed": int(spec.scenario.get("seed", 0)),
            "gen_version": int(spec.scenario.get("gen_version", 1)),
            "landscape": spec.scenario.get("landscape", ""),
            "map_size": 2 ** int(spec.scenario.get("map_log2", 7)),
            "harbor_name": f"{ORG}/railhead-{spec.task_id}",
            "docker_image": image,
            "title": spec.title,
            "instruction": (hdir / "instruction.md").read_text() if (hdir / "instruction.md").exists() else spec.briefing,
            "horizon_days": spec.horizon_days,
            "runoff_days": spec.runoff_days,
            "limits": json.dumps(spec.limits),
            "objective": json.dumps(spec.objective),
            "scenario": json.dumps(spec.scenario),
            "idle_reward": float(certs.get("idle_reward", 0.0)),
            "oracle_reward": float(certs.get("oracle_reward", 0.0)),
            "start_sav": (tdir / "start.sav").read_bytes(),
            "task_binary": _tar_dir(hdir) if hdir.exists() else b"",
        })
    return rows


def _trajectory_row(tdir: str, log: str, policy: str, credit: bool) -> dict:
    from railhead.actionlog import read_log
    from railhead.verify import turn_credit, verify

    tdir_p, log_p = Path(tdir), Path(log)
    spec = TaskSpec.load(tdir_p)
    work = Path(tempfile.mkdtemp(prefix="rh-traj-"))
    try:
        out = verify(tdir_p, tdir_p / "start.sav", log_p, work / "v")
        credits = turn_credit(spec, tdir_p / "start.sav", log_p, work, out["reward"]) if credit else []
    finally:
        import shutil

        shutil.rmtree(work, ignore_errors=True)
    recs = [r.raw for r in read_log(log_p)]
    return {
        "task_id": spec.task_id, "family": spec.family, "split": spec.split, "policy": policy,
        "actions_jsonl": log_p.read_text(),
        "n_acts": sum(1 for r in recs if r["kind"] == "act"),
        "n_waits": sum(1 for r in recs if r["kind"] == "wait"),
        "reward": float(out["reward"]),
        "valid": int(out["valid"]),
        "components": json.dumps(out),
        "turn_credit": json.dumps(credits),
    }


def trajectory_rows(tasks_dir: Path, credit: bool = False, jobs: int = 8,
                    extra: list[tuple[str, str, str]] | None = None) -> list[dict]:
    """Oracle trajectories for every task plus ``extra`` (task_dir, log, policy) model games."""
    from concurrent.futures import ProcessPoolExecutor

    items = [(str(t), str(t / "oracle.jsonl"), "oracle") for t in sorted(Path(tasks_dir).iterdir())
             if (t / "oracle.jsonl").exists()]
    items += list(extra or [])
    with ProcessPoolExecutor(max_workers=jobs) as ex:
        return list(ex.map(_trajectory_row, *zip(*items), [credit] * len(items)))


def write_parquet(rows: list[dict], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")


def build(tasks_dir: Path, harbor_dir: Path, out_dir: Path, credit: bool = False, jobs: int = 8,
          extra: list[tuple[str, str, str]] | None = None, image: str = DEFAULT_IMAGE) -> dict:
    out_dir = Path(out_dir)
    tasks = task_rows(tasks_dir, harbor_dir, image=image)
    by_split: dict[str, list[dict]] = {}
    for r in tasks:
        by_split.setdefault(r["split"], []).append(r)
    for split, rows in by_split.items():
        write_parquet(rows, out_dir / "tasks" / f"{split}.parquet")
    trajs = trajectory_rows(tasks_dir, credit=credit, jobs=jobs, extra=extra)
    tsplit: dict[str, list[dict]] = {}
    for r in trajs:
        tsplit.setdefault(r["split"], []).append(r)
    for split, rows in tsplit.items():
        write_parquet(rows, out_dir / "trajectories" / f"{split}.parquet")
    counts = {"tasks": {k: len(v) for k, v in by_split.items()}, "trajectories": {k: len(v) for k, v in tsplit.items()}}
    write_card(out_dir, counts, image=image)
    return counts


def push(out_dir: Path, repo: str = "rachittshah/railhead", private: bool = False) -> str:
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(repo, repo_type="dataset", exist_ok=True, private=private)
    api.upload_folder(folder_path=str(out_dir), repo_id=repo, repo_type="dataset",
                      commit_message="Railhead tasks + oracle trajectories")
    return f"https://huggingface.co/datasets/{repo}"


CARD = """---
license: mit
pretty_name: Railhead
task_categories:
- reinforcement-learning
tags:
- openttd
- rl-environment
- agents
- harbor
- long-horizon
- simulation
- verifiable-rewards
configs:
- config_name: tasks
  data_files:
{task_files}
- config_name: trajectories
  data_files:
{traj_files}
---

# Railhead

Railhead is a reinforcement learning (RL) environment.
In Railhead, a language model plays the transport game OpenTTD directly.
The model controls one company with the same commands that a human player uses.
This dataset contains the Railhead tasks and the oracle trajectories.
The documents of Railhead follow ASD-STE100 Simplified Technical English.

* Code: https://github.com/rachittshah/railhead
* Environment image: `{image}`
* Harbor registry: `rachittshah/railhead` and one task package for each task

## Contents

| Config | Split | Rows |
|---|---|---|
{count_rows}

### Config `tasks`

Each row is one task. The important columns are:

| Column | Description |
|---|---|
| `task_id` | The identifier of the task. It is the same in Harbor and in the image. |
| `family`, `split` | The task family and the split (`train`, `dev`, or `ood`). |
| `instruction` | The text that the agent receives. |
| `objective`, `scenario`, `limits` | The task specification (JSON). |
| `idle_reward`, `oracle_reward` | The certificates: the reward for no play and for the oracle. |
| `start_sav` | The sealed start state (an OpenTTD savegame). |
| `task_binary` | The complete Harbor task directory (`tar.gz`), with the tests and the oracle solution. |

### Config `trajectories`

Each row is one oracle trajectory.
The `actions_jsonl` column is the action log.
The verifier replays this log from `start_sav` and calculates the reward.
The `components` column contains the reward and the diagnostic values.
The `turn_credit` column contains the leave-one-turn-out credit for each turn.

## Task families

| Family | Objective | Reward |
|---|---|---|
| `deliver` | Deliver a quantity of a cargo to one industry in 300 days. | `min(1, D / Q)` |
| `deliver_bid` | The `deliver` objective with a promise of a quantity. | A proper newsvendor score, linear in 0 to 1 |
| `impossible` | A contract that no company can complete. | 1 if the agent abstains, else 0 |
| `simops` | Repair a truck route that has faults. | Run-off deliveries between the broken and the repaired network |
| `tycoon` | Open play for 365 days. | Net worth after a 730-day run-off, 0.5 at the scripted baseline |

The `ood` split uses the arctic and tropic climates.
The cargo chains of these climates are not in the `train` split.

## Use the tasks

1. Pull the image: `docker pull {image}`.
2. Extract `task_binary` of a row to a directory.
3. Run the task with Harbor: `harbor run -p <task_dir> -a <agent> -m <model>`.

The reward is in `verifier/reward.json`.
The verifier replays the action log of the agent from the sealed start state.
A change to the live game that is not in the action log has no effect on the reward.

## License

The dataset and the Railhead code have the MIT license.
The environment image contains OpenTTD, which has the GPL-2.0 license.
"""


def write_card(out_dir: Path, counts: dict, image: str = DEFAULT_IMAGE) -> Path:
    out_dir = Path(out_dir)
    tf = "\n".join(f"  - split: {s}\n    path: tasks/{s}.parquet" for s in sorted(counts["tasks"]))
    jf = "\n".join(f"  - split: {s}\n    path: trajectories/{s}.parquet" for s in sorted(counts["trajectories"]))
    rows = [f"| tasks | {s} | {n} |" for s, n in sorted(counts["tasks"].items())]
    rows += [f"| trajectories | {s} | {n} |" for s, n in sorted(counts["trajectories"].items())]
    text = CARD.format(task_files=tf, traj_files=jf, count_rows="\n".join(rows), image=image)
    (out_dir / "README.md").write_text(text)
    return out_dir / "README.md"
