"""Locate the (patched) OpenTTD engine and Railhead's bundled scripts.

Resolution order for the binary:
  1. ``$RAILHEAD_OPENTTD``
  2. ``/opt/railhead/engine/openttd`` (Docker image layout)
  3. ``<repo>/.cache/openttd-src/build/openttd`` (dev checkout, built by engine/build.sh)
The engine dir (binary's parent) must hold ``lang/``, ``baseset/`` (incl. OpenGFX), ``ai/``, ``game/``.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_DIR = PACKAGE_DIR.parent.parent  # valid for editable/dev installs only


class EngineNotFound(FileNotFoundError):
    pass


def find_openttd() -> Path:
    candidates = []
    if env := os.environ.get("RAILHEAD_OPENTTD"):
        candidates.append(Path(env))
    candidates += [Path("/opt/railhead/engine/openttd"), REPO_DIR / ".cache/openttd-src/build/openttd"]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    raise EngineNotFound(
        "OpenTTD engine not found. Build it with engine/build.sh or set RAILHEAD_OPENTTD. Tried: "
        + ", ".join(str(c) for c in candidates)
    )


def scripts_dir() -> Path:
    """Directory holding Railhead's Squirrel scripts: ``game/RailheadBridge`` and ``ai/RailheadIdle``."""
    if env := os.environ.get("RAILHEAD_SCRIPTS"):
        return Path(env)
    for c in (PACKAGE_DIR / "scripts", REPO_DIR / "gamescript"):
        if c.is_dir():
            return c
    raise EngineNotFound("Railhead Squirrel scripts not found (set RAILHEAD_SCRIPTS)")
