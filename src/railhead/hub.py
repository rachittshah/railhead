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


def trajectory_rows(tasks_dir: Path, credit: bool = False) -> list[dict]:
    from railhead.actionlog import read_log
    from railhead.verify import turn_credit, verify

    rows = []
    for tdir in sorted(p for p in Path(tasks_dir).iterdir() if (p / "oracle.jsonl").exists()):
        spec = TaskSpec.load(tdir)
        log = tdir / "oracle.jsonl"
        work = Path(tempfile.mkdtemp(prefix="rh-traj-"))
        out = verify(tdir, tdir / "start.sav", log, work / "v")
        credits = turn_credit(spec, tdir / "start.sav", log, work, out["reward"]) if credit else []
        recs = [r.raw for r in read_log(log)]
        rows.append({
            "task_id": spec.task_id, "family": spec.family, "split": spec.split, "policy": "oracle",
            "actions_jsonl": log.read_text(),
            "n_acts": sum(1 for r in recs if r["kind"] == "act"),
            "n_waits": sum(1 for r in recs if r["kind"] == "wait"),
            "reward": float(out["reward"]),
            "components": json.dumps(out),
            "turn_credit": json.dumps(credits),
        })
    return rows


def write_parquet(rows: list[dict], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")


def build(tasks_dir: Path, harbor_dir: Path, out_dir: Path, credit: bool = False) -> dict:
    out_dir = Path(out_dir)
    tasks = task_rows(tasks_dir, harbor_dir)
    by_split: dict[str, list[dict]] = {}
    for r in tasks:
        by_split.setdefault(r["split"], []).append(r)
    for split, rows in by_split.items():
        write_parquet(rows, out_dir / "tasks" / f"{split}.parquet")
    trajs = trajectory_rows(tasks_dir, credit=credit)
    tsplit: dict[str, list[dict]] = {}
    for r in trajs:
        tsplit.setdefault(r["split"], []).append(r)
    for split, rows in tsplit.items():
        write_parquet(rows, out_dir / "trajectories" / f"{split}.parquet")
    return {"tasks": {k: len(v) for k, v in by_split.items()}, "trajectories": {k: len(v) for k, v in tsplit.items()}}


def push(out_dir: Path, repo: str = "rachittshah/railhead", private: bool = False) -> str:
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(repo, repo_type="dataset", exist_ok=True, private=private)
    api.upload_folder(folder_path=str(out_dir), repo_id=repo, repo_type="dataset",
                      commit_message="Railhead tasks + oracle trajectories")
    return f"https://huggingface.co/datasets/{repo}"
