"""Deterministic replay of an action log from a sealed start savegame (proof-of-play).

Every ``act`` record is re-issued at exactly its recorded tick and every ``wait`` re-runs exactly
``end_ticks - ticks`` ticks (so event-driven early stops replay bit-for-bit). Live checksums
recorded after waits are compared with the replay; any mismatch is a *divergence* (infra problem,
reported as valid=False upstream — never silently scored).

``on_wait(world, record)`` (optional) runs once after each replayed wait, after its checksum check,
while the world stands at the wait's ``end_ticks``: a hook to observe every decision point of a game.
It is not called for a wait that ``max_ticks`` cut short.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from railhead.actionlog import read_log
from railhead.bridge import BridgeError
from railhead.world import World


class ReplayRejected(ValueError):
    """The log contains something a player could not have done (non-whitelisted command, time travel)."""


@dataclass
class ReplayResult:
    valid: bool
    final_checksum: dict | None
    divergences: list[dict] = field(default_factory=list)
    acts: int = 0
    failed_acts: int = 0
    waits: int = 0
    end_ticks: int = 0
    world: World | None = None


def default_whitelist() -> set[str] | None:
    try:
        from railhead.actions import WHITELIST
    except ImportError:
        return None
    return set(WHITELIST)


def replay(
    start_save: Path,
    log_path: Path,
    workdir: Path,
    allowed: set[str] | None = None,
    keep_open: bool = False,
    max_ticks: int | None = None,
    on_wait: Callable[[World, dict], None] | None = None,
) -> ReplayResult:
    allowed = default_whitelist() if allowed is None else allowed
    world = World.load(Path(workdir), Path(start_save))
    result = ReplayResult(valid=True, final_checksum=None)
    try:
        ticks = world.state()["ticks"]
        for rec in read_log(log_path):
            r = rec.raw
            if rec.kind in ("header", "meta"):
                continue
            if rec.ticks != ticks:
                raise ReplayRejected(f"record seq={r.get('seq')} at tick {rec.ticks}, world is at {ticks}")
            if max_ticks is not None and ticks >= max_ticks:
                break
            if rec.kind == "act":
                m = r["m"]
                if allowed is None or m not in allowed:
                    raise ReplayRejected(f"command {m!r} is not a player action")
                result.acts += 1
                try:
                    world.call(m, **(r.get("a") or {}))
                    ok = True
                except BridgeError:
                    ok = False
                    result.failed_acts += 1
                if ok != bool(r.get("ok")):
                    result.divergences.append({"seq": r.get("seq"), "what": "act outcome", "live": r.get("ok"), "replay": ok})
            elif rec.kind == "wait":
                n = int(r["end_ticks"]) - int(r["ticks"])
                if n <= 0:
                    raise ReplayRejected(f"wait seq={r.get('seq')} does not advance time")
                if max_ticks is not None and ticks + n > max_ticks:
                    n = max_ticks - ticks
                world.step(ticks=n)
                result.waits += 1
                ticks = world.state()["ticks"]
                if "checksum" in r and ticks == r["end_ticks"]:
                    got = world.call("checksum")
                    if got.get("h") != r["checksum"].get("h"):
                        result.divergences.append({"seq": r.get("seq"), "what": "checksum", "live": r["checksum"], "replay": got})
                if on_wait is not None and ticks == r["end_ticks"]:
                    on_wait(world, r)
            else:
                raise ReplayRejected(f"unknown record kind {rec.kind!r}")
        result.end_ticks = world.state()["ticks"]
        result.final_checksum = world.call("checksum")
        result.valid = not result.divergences
        if keep_open:
            result.world = world
            world = None  # type: ignore[assignment]
        return result
    finally:
        if world is not None:
            world.close()
