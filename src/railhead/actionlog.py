"""Append-only action log — the trajectory *is* the proof (proof-of-play, DESIGN.md M1).

JSONL, one record per line:
  {"kind":"header","v":1,"company":C,"ticks":T,"checksum":H}
  {"kind":"act","seq":n,"ticks":T,"m":"road_line","a":{...},"ok":true,"ret":{...}|"code":"ERR_.."}
  {"kind":"wait","seq":n,"ticks":T0,"end_ticks":T1,"a":{"days":30,"until":[...]},"reason":"elapsed","checksum":H}
  {"kind":"meta","seq":n,"ticks":T,"m":"forecast|promise|abstain|flag|submit","a":{...}}
Replay needs only: the sealed start savegame + this file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

LOG_VERSION = 1


class ActionLog:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "a", encoding="utf-8")
        self._seq = sum(1 for _ in open(self.path, encoding="utf-8")) if self.path.stat().st_size else 0

    def write(self, record: dict[str, Any]) -> dict[str, Any]:
        rec = dict(record)
        if rec.get("kind") != "header":
            rec["seq"] = self._seq
        self._seq += 1
        self._fh.write(json.dumps(rec, separators=(",", ":"), sort_keys=True) + "\n")
        self._fh.flush()
        return rec

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()


@dataclass
class Record:
    kind: str
    raw: dict[str, Any]

    @property
    def ticks(self) -> int:
        return int(self.raw.get("ticks", 0))


def read_log(path: Path) -> Iterator[Record]:
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{n}: not JSON ({e})") from None
            if not isinstance(raw, dict) or "kind" not in raw:
                raise ValueError(f"{path}:{n}: record without kind")
            yield Record(kind=raw["kind"], raw=raw)
