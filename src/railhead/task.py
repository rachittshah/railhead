"""Task specs and the on-disk task catalog.

A task directory holds ``task.json`` (this spec) and ``start.sav`` (the sealed start state: company
founded, objective monitors installed, world paused). The image bakes every task under
``$RAILHEAD_TASKS`` and Harbor picks one with ``RAILHEAD_TASK``.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from railhead.engine import REPO_DIR


@dataclass
class TaskSpec:
    task_id: str
    family: str
    horizon_days: int
    runoff_days: int = 365
    limits: dict = field(default_factory=dict)  # max_forks, max_fork_days, allow_exact_forks
    objective: dict = field(default_factory=dict)
    scenario: dict = field(default_factory=dict)
    title: str = ""
    briefing: str = ""
    split: str = "train"
    version: int = 1
    # Verifier-only facts (feasibility, oracle results, injected faults). Never baked into the image:
    # stored in secret.json, which ships only in Harbor's tests/ (uploaded after the agent finishes).
    secret: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("secret")
        return d

    @property
    def full_objective(self) -> dict:
        return {**self.objective, **self.secret}

    @classmethod
    def from_json(cls, d: dict) -> TaskSpec:
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def load(cls, task_dir: Path) -> TaskSpec:
        task_dir = Path(task_dir)
        spec = cls.from_json(json.loads((task_dir / "task.json").read_text()))
        if (task_dir / "secret.json").exists():
            spec.secret = json.loads((task_dir / "secret.json").read_text())
        return spec

    def save(self, task_dir: Path) -> None:
        task_dir = Path(task_dir)
        (task_dir / "task.json").write_text(json.dumps(self.to_json(), indent=1))
        (task_dir / "secret.json").write_text(json.dumps(self.secret, indent=1))

    @property
    def max_forks(self) -> int:
        return int(self.limits.get("max_forks", 2))

    @property
    def max_fork_days(self) -> int:
        return int(self.limits.get("max_fork_days", 365))


def tasks_root() -> Path:
    if env := os.environ.get("RAILHEAD_TASKS"):
        return Path(env)
    for c in (Path("/opt/railhead/tasks"), REPO_DIR / "tasks"):
        if c.is_dir():
            return c
    return REPO_DIR / "tasks"


def state_root() -> Path:
    return Path(os.environ.get("RAILHEAD_STATE", Path.home() / ".railhead" / "state"))


def task_dir(task_id: str) -> Path:
    d = tasks_root() / task_id
    if not (d / "task.json").exists():
        raise FileNotFoundError(f"unknown task {task_id!r} (looked in {tasks_root()})")
    return d
