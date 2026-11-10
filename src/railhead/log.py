"""One structured-logging setup for the operator tools (A/B harness, behaviour metrics, arena runner).

Every record is one JSON line: ``{"ts": <epoch>, "time": <ISO UTC>, "level", "logger", "msg", ...fields}``.
The level comes from ``RAILHEAD_LOG_LEVEL`` (DEBUG, INFO, WARNING, ERROR; default INFO). The file is the
path given to ``setup`` (an experiment or arena directory), else ``<state root>/logs/railhead.jsonl``.

    from railhead.log import get_logger, setup
    setup(Path("experiments/x/harness.jsonl"))
    log = get_logger("ab")
    log.info("cell started", extra={"fields": {"cell": "control/t/m/r1"}})   # or: event(log, "msg", cell=...)

The core runtime (daemon, world, bridge, server, replay, verify) does not use this module.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = "railhead"
_HANDLERS: dict[str, logging.Handler] = {}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out = {"ts": round(record.created, 3),
               "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
               "level": record.levelname, "logger": record.name, "msg": record.getMessage()}
        out.update(getattr(record, "fields", None) or {})
        if record.exc_info:
            out["exc"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, separators=(",", ":"))


def level() -> int:
    lv = logging.getLevelName(os.environ.get("RAILHEAD_LOG_LEVEL", "INFO").upper())
    return lv if isinstance(lv, int) else logging.INFO


def default_path() -> Path:
    from railhead.task import state_root

    return state_root() / "logs" / "railhead.jsonl"


def setup(path: Path | None = None) -> Path:
    """Send every ``railhead.*`` logger to ``path`` as JSON lines (idempotent per path). Returns the path."""
    path = Path(path or default_path()).resolve()
    root = logging.getLogger(ROOT)
    root.setLevel(level())
    root.propagate = False
    if str(path) not in _HANDLERS:
        path.parent.mkdir(parents=True, exist_ok=True)
        h = logging.FileHandler(path, encoding="utf-8")
        h.setFormatter(JsonFormatter())
        root.addHandler(h)
        _HANDLERS[str(path)] = h
    return path


def close(path: Path | None = None) -> None:
    """Detach and close the handler of ``path`` (all handlers when None)."""
    root = logging.getLogger(ROOT)
    keys = [str(Path(path).resolve())] if path else list(_HANDLERS)
    for k in keys:
        h = _HANDLERS.pop(k, None)
        if h:
            root.removeHandler(h)
            h.close()


def get_logger(name: str) -> logging.Logger:
    """A child of the ``railhead`` logger. Records go nowhere until ``setup`` is called (no stderr noise)."""
    root = logging.getLogger(ROOT)
    if not root.handlers:
        root.addHandler(logging.NullHandler())
        root.propagate = False
    return logging.getLogger(f"{ROOT}.{name}")


def event(logger: logging.Logger, msg: str, lvl: int = logging.INFO, **fields) -> None:
    """Log ``msg`` with structured ``fields`` (they become top-level keys of the JSON line)."""
    logger.log(lvl, msg, extra={"fields": fields})


def now_iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(time.time() if ts is None else ts, UTC).isoformat(timespec="seconds")
