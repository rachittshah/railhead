"""Session hooks for the test suite.

``RAILHEAD_EX_CAPTURE=<dir>``: record every exchange record sequence the tests feed ``Exchange.apply`` and
write them to ``<dir>/exchange_captured.jsonl`` (one JSON array of records per line, sorted, no duplicates),
the ``captured`` kind of railhead.exchange.corpus. Per exchange instance it keeps the full sequence of
accepted input records, and for every rejected record the accepted prefix plus that record. The file is
large (~360 MB) and not committed; make it with ``railhead.exchange.corpus.run_capture(dir)`` or::

    RAILHEAD_EX_CAPTURE=/tmp/cap .venv/bin/python -m pytest -q \\
        tests/unit/test_exchange_*.py tests/unit/test_multi_market.py tests/unit/test_waybill_*.py

Without the variable this module does nothing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

CAPTURE_DIR = os.environ.get("RAILHEAD_EX_CAPTURE")
CAPTURE_FILE = "exchange_captured.jsonl"
_SKIP = object()
_sequences: dict[int, list[Any]] = {}    # id -> accepted inputs of one exchange (kept alive: ids stay unique)
_rejected: list[list[Any]] = []           # accepted prefix + the rejected record


def _wire(record: Any) -> Any:
    """The record as JSON would carry it, or _SKIP when it is not serialisable."""
    try:
        return json.loads(json.dumps(record))
    except (TypeError, ValueError, RecursionError):
        return _SKIP


def pytest_configure(config: Any) -> None:
    if not CAPTURE_DIR:
        return
    from railhead.exchange.exchange import Exchange

    original = Exchange.apply

    def apply(self: Exchange, record: dict[str, Any]) -> dict[str, Any]:
        # The inputs live on the instance, so copy.deepcopy(exchange) continues from its own copy of them.
        seq = self.__dict__.setdefault("_capture_inputs", [])
        if seq is not None and id(seq) not in _sequences:
            _sequences[id(seq)] = seq
        snap = _wire(record)  # before the call: apply must see (and may reject) the original object
        try:
            out = original(self, record)
        except BaseException:
            if snap is not _SKIP and seq is not None:
                _rejected.append([*seq, snap])
            raise
        if snap is _SKIP:
            self._capture_inputs = None  # type: ignore[attr-defined]
        elif seq is not None:
            seq.append(snap)
        return out

    Exchange.apply = apply  # type: ignore[method-assign]


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    if not CAPTURE_DIR:
        return
    lines = {json.dumps(case) for case in [*(s for s in _sequences.values() if s), *_rejected]}
    out = Path(CAPTURE_DIR)
    out.mkdir(parents=True, exist_ok=True)
    (out / CAPTURE_FILE).write_text("".join(f"{ln}\n" for ln in sorted(lines)), encoding="ascii")
