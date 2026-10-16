"""Seal a task: record sha256 of everything the verifier must trust (start state, engine, scripts, package).

In Harbor's shared verifier mode the agent had root in the same container, so ``tests/test.sh``
first checks ``sealed.sha256`` with coreutils (``sha256sum -c``) *before* any Python runs, and the
Python verifier re-checks the start state. Trust boundary: the base OS image.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from railhead.engine import PACKAGE_DIR, find_openttd, scripts_dir


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def trusted_files() -> list[Path]:
    """Engine binary + every GS/AI script + every railhead module the verifier imports."""
    files = [find_openttd()]
    files += sorted(p for p in scripts_dir().rglob("*.nut") if p.is_file())
    files += sorted(p for p in PACKAGE_DIR.rglob("*.py") if p.is_file())
    return [f.resolve() for f in files]


def seal_task(task_dir: Path) -> dict:
    task_dir = Path(task_dir)
    sealed = {"start_sav": file_sha256(task_dir / "start.sav"),
              "task_json": file_sha256(task_dir / "task.json")}
    if (task_dir / "secret.json").exists():
        sealed["secret_json"] = file_sha256(task_dir / "secret.json")
    (task_dir / "sealed.json").write_text(json.dumps(sealed, indent=1))
    return sealed


def write_sha256_manifest(dest: Path, files: list[Path] | None = None) -> Path:
    """``sha256sum -c``-compatible manifest of trusted files (absolute paths)."""
    lines = [f"{file_sha256(f)}  {f}" for f in (files or trusted_files())]
    Path(dest).write_text("\n".join(lines) + "\n")
    return Path(dest)


def check_start(task_dir: Path, start_sav: Path) -> bool:
    sealed_path = Path(task_dir) / "sealed.json"
    if not sealed_path.exists():
        return True
    sealed = json.loads(sealed_path.read_text())
    return file_sha256(Path(start_sav)) == sealed.get("start_sav")
