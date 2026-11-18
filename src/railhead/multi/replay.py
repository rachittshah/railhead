"""Replay of a merged v3 log from the sealed multi-company start (docs/WAYBILL.md, section 5).

The replay checks every record with :class:`railhead.multi.log.LogState` before it applies it, then:

* ``act``: binds the record's company and re-issues the action with its recorded arguments. A
  different outcome (``ok``) is a divergence.
* ``meta``: sends the v3 digest (company, decision, arguments) to the GameScript, as the live world did.
* ``wait_req``: no effect on the world (checked for structure only).
* ``step``: runs exactly ``end_ticks - ticks`` ticks and compares the checksum. A difference is a
  divergence.

The header ticks must be the start tick (else ``ReplayRejected``); a different header checksum is a
divergence. The result has the net worth of each company of the header at the end of the replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from railhead.actionlog import read_log
from railhead.bridge import BridgeError
from railhead.multi.log import TICKS_PER_DAY, LogState, meta_digest_v3
from railhead.multi.world import SharedWorld
from railhead.replay import ReplayRejected, _meta_names, default_whitelist, meta_args_problem
from railhead.world import World


@dataclass
class MultiReplayResult:
    valid: bool
    final_checksum: dict | None
    divergences: list[dict] = field(default_factory=list)
    acts: int = 0
    failed_acts: int = 0
    steps: int = 0
    end_ticks: int = 0
    start_ticks: int = 0
    start_date: int = 0
    companies: list[dict] = field(default_factory=list)
    net_worth: dict[int, int] = field(default_factory=dict)
    checkpoints: list[tuple] = field(default_factory=list)
    metas: list[dict] = field(default_factory=list)
    state: LogState | None = None
    world: SharedWorld | None = None


def horizon_tick(start_ticks: int, horizon_days: int | None) -> int | None:
    return None if horizon_days is None else start_ticks + int(horizon_days) * TICKS_PER_DAY


def replay_multi(start_save: Path, log_path: Path, workdir: Path, allowed: set[str] | None = None,
                 keep_open: bool = False, max_ticks: int | None = None,
                 horizon_days: int | None = None) -> MultiReplayResult:
    allowed = default_whitelist() if allowed is None else allowed
    records = [r.raw for r in read_log(log_path)]  # read once: the checks and the replay see the same bytes
    if not records:
        raise ReplayRejected("empty log")
    metas = _meta_names()
    world = World.load(Path(workdir), Path(start_save))
    sw: SharedWorld | None = None
    try:
        st = world.state()
        start, date = int(st["ticks"]), int(st["date"])
        state = LogState(records[0], horizon_tick=horizon_tick(start, horizon_days))
        if state.now != start:
            raise ReplayRejected(f"header tick {state.now} is not the start tick {start}")
        sw = SharedWorld(world, list(state.companies.values()))
        res = MultiReplayResult(valid=True, final_checksum=None, start_ticks=start, start_date=date,
                                companies=list(state.companies.values()), state=state)
        logged, got = records[0].get("checksum"), world.call("checksum")
        if not isinstance(logged, dict):
            raise ReplayRejected("v3 header has no checksum")
        res.checkpoints.append(("header", 0, got.get("h")))
        if logged.get("h") != got.get("h"):
            res.divergences.append({"seq": 0, "what": "header checksum", "live": logged, "replay": got,
                                    "key": ("header", 0), "stop": start + 1})
        for r in records[1:]:
            if max_ticks is not None and sw.ticks >= max_ticks:
                break
            state.check(r)  # LogRejected is a ReplayRejected
            seq, kind = r["seq"], r["kind"]
            if kind == "act":
                m = r["m"]
                if allowed is None or m not in allowed:
                    raise ReplayRejected(f"command {m!r} is not a player action")
                res.acts += 1
                try:
                    sw.bind(r["company"])
                    world.call_args(m, r.get("a") or {})
                    ok = True
                except BridgeError:
                    ok = False
                    res.failed_acts += 1
                res.checkpoints.append(("act", seq, ok))
                if ok != bool(r.get("ok")):
                    res.divergences.append({"seq": seq, "what": "act outcome", "live": r.get("ok"), "replay": ok,
                                            "key": ("act", seq), "stop": sw.ticks + 1})
            elif kind == "meta":
                m, a = r["m"], r.get("a") or {}
                if m not in metas:
                    raise ReplayRejected(f"meta seq={seq}: unknown decision {m!r}")
                problem = meta_args_problem(m, a, require_confirm=False)
                if problem:
                    raise ReplayRejected(f"meta seq={seq}: {problem}")
                world.call_args("note", {"d": meta_digest_v3(r["company"], m, a)})
                res.metas.append(dict(r, a=dict(a)))
            elif kind == "step":
                end = r["end_ticks"]
                n = end - sw.ticks
                if max_ticks is not None and sw.ticks + n > max_ticks:
                    n = max_ticks - sw.ticks
                sw.advance(n)
                res.steps += 1
                if sw.ticks == end:
                    got = world.call("checksum")
                    res.checkpoints.append(("step", seq, got.get("h")))
                    if got.get("h") != r["checksum"].get("h"):
                        res.divergences.append({"seq": seq, "what": "checksum", "live": r["checksum"],
                                                "replay": got, "key": ("step", seq), "stop": end})
        res.end_ticks = sw.ticks
        res.final_checksum = world.call("checksum")
        res.net_worth = sw.net_worth()
        res.valid = not res.divergences
        if keep_open:
            res.world = sw
            sw = None
            world = None  # type: ignore[assignment]
        return res
    finally:
        if world is not None:
            world.close()
