"""Deterministic replay of an action log from a sealed start savegame (proof-of-play).

Every ``act`` record is re-issued at exactly its recorded tick and every ``wait`` re-runs exactly
``end_ticks - ticks`` ticks (so event-driven early stops replay bit-for-bit). Live checksums
recorded after waits are compared with the replay; any mismatch is a *divergence*. The verifier
replays a second time to attribute it: replays that agree with each other but not with the log mean
a forged log (rejected); replays that disagree mean engine nondeterminism (valid=False).

The replay rejects (``ReplayRejected``) a log that no honest daemon writes:

* the first record is not a header, or the header version is not an int in
  ``[min_version, LOG_VERSION]`` (a header-only log is "no play" and passes at any version);
* a ``seq`` that is not contiguous (the first record has seq 0 or 1, then +1 per record);
* any record, ``meta`` included, whose ``ticks`` is not the replayed world's tick at that position.
  A v1 log may count ticks from its header tick (the generator's abstain and bid oracles have a
  header at tick 0); the replay then adds ``start tick - header tick`` to every record, and returns
  the meta records with replayed ticks in ``ReplayResult.metas`` (deadlines must use these);
* an ``act`` outside the whitelist, or on or after the horizon date; a ``meta`` with an unknown name;
* a ``wait`` without a checksum, that does not move time forward, or that ends past the horizon
  (checked before the ticks are simulated, so a forged 100-year wait costs nothing);
* v2 only: header ticks that differ from the start state (a different header checksum is a
  divergence, attributed like the others).

In a v2 log each meta record is sent to the GameScript as a digest (``note``), as the live world did,
so a meta record inserted before a wait changes that wait's checksum.

``on_wait(world, record)`` (optional) runs once after each replayed wait, after its checksum check,
while the world stands at the wait's ``end_ticks``: a hook to observe every decision point of a game.
It is not called for a wait that ``max_ticks`` cut short.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from railhead.actionlog import LOG_VERSION, meta_digest, read_log
from railhead.bridge import BridgeError
from railhead.world import World

TICKS_PER_DAY = 74


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
    version: int = LOG_VERSION
    start_ticks: int = 0
    start_date: int = 0
    # What this replay computed at each check point: ("act", seq, ok) and ("wait", seq, checksum h).
    # Two replays of one log must give the same list, or the engine is not deterministic.
    checkpoints: list[tuple] = field(default_factory=list)
    # The replayed meta records, with "ticks" in the replayed world's frame.
    metas: list[dict] = field(default_factory=list)


def default_whitelist() -> set[str] | None:
    try:
        from railhead.actions import WHITELIST
    except ImportError:
        return None
    return set(WHITELIST)


def _meta_names() -> set[str]:
    from railhead.actions import META_SCHEMAS

    return set(META_SCHEMAS)


def _int(v: object, what: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int):
        raise ReplayRejected(f"{what} must be an integer, got {v!r}")
    return v


def replay(
    start_save: Path,
    log_path: Path,
    workdir: Path,
    allowed: set[str] | None = None,
    keep_open: bool = False,
    max_ticks: int | None = None,
    on_wait: Callable[[World, dict], None] | None = None,
    min_version: int = 1,
    horizon_days: int | None = None,
) -> ReplayResult:
    allowed = default_whitelist() if allowed is None else allowed
    records = list(read_log(log_path))  # read once: the checks and the replay see the same bytes
    if not records or records[0].kind != "header":
        raise ReplayRejected("the first record of the log is not a header")
    header = records[0].raw
    version = header.get("v")
    if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= LOG_VERSION:
        raise ReplayRejected(f"unknown log version {version!r}")
    if version < min_version and len(records) > 1:
        raise ReplayRejected(f"log version {version} is older than the minimum {min_version}")
    metas = _meta_names()
    world = World.load(Path(workdir), Path(start_save))
    result = ReplayResult(valid=True, final_checksum=None, version=version)
    try:
        st = world.state()
        ticks, date = int(st["ticks"]), int(st["date"])
        result.start_ticks, result.start_date = ticks, date
        end_date = cap_ticks = None
        if horizon_days is not None:
            end_date = date + int(horizon_days)
            # A wait capped at the days left can end up to one day past start + horizon in ticks when
            # it starts mid-day; the date check in verify stays the exact rule.
            cap_ticks = ticks + (int(horizon_days) + 1) * TICKS_PER_DAY
        offset = ticks - _int(header.get("ticks", 0), "header ticks")  # v1 only: a log in its header's tick frame
        if version >= 2 and len(records) > 1:
            if offset != 0:
                raise ReplayRejected(f"header tick {header.get('ticks')!r} is not the start tick {ticks}")
            logged, got = header.get("checksum"), world.call("checksum")
            if not isinstance(logged, dict):
                raise ReplayRejected("v2 header has no checksum")
            result.checkpoints.append(("header", 0, got.get("h")))
            if logged.get("h") != got.get("h"):  # attributed like any other divergence
                result.divergences.append({"seq": 0, "what": "header checksum", "live": logged, "replay": got,
                                           "key": ("header", 0), "stop": ticks + 1})
        expect_seq: int | None = None
        for rec in records[1:]:
            r = rec.raw
            seq = r.get("seq")
            if isinstance(seq, bool) or not isinstance(seq, int) or (
                    seq not in (0, 1) if expect_seq is None else seq != expect_seq):
                raise ReplayRejected(f"record seq={seq!r} is out of sequence (expected {expect_seq or '0 or 1'})")
            expect_seq = seq + 1
            if rec.kind == "header":
                raise ReplayRejected(f"second header at seq={seq}")
            if max_ticks is not None and ticks >= max_ticks:
                break
            if _int(r.get("ticks"), f"ticks of seq={seq}") + offset != ticks:
                raise ReplayRejected(f"record seq={seq} at tick {r.get('ticks')}, world is at {ticks - offset}")
            if rec.kind == "meta":
                m = r.get("m")
                if m not in metas or not isinstance(r.get("a", {}), dict):
                    raise ReplayRejected(f"meta seq={seq}: unknown decision {m!r}")
                if version >= 2:
                    world.call_args("note", {"d": meta_digest(m, r.get("a") or {})})
                result.metas.append(dict(r, ticks=ticks, a=dict(r.get("a") or {})))
            elif rec.kind == "act":
                m = r.get("m")
                if allowed is None or m not in allowed:
                    raise ReplayRejected(f"command {m!r} is not a player action")
                if end_date is not None and date >= end_date:
                    raise ReplayRejected(f"act seq={seq} on or after the horizon date")
                a = r.get("a") or {}
                if not isinstance(a, dict):
                    raise ReplayRejected(f"act seq={seq}: arguments are not an object")
                result.acts += 1
                try:
                    world.call_args(m, a)
                    ok = True
                except BridgeError:
                    ok = False
                    result.failed_acts += 1
                result.checkpoints.append(("act", seq, ok))
                if ok != bool(r.get("ok")):
                    result.divergences.append({"seq": seq, "what": "act outcome", "live": r.get("ok"), "replay": ok,
                                               "key": ("act", seq), "stop": ticks + 1})
            elif rec.kind == "wait":
                end = _int(r.get("end_ticks"), f"end_ticks of seq={seq}") + offset
                logged = r.get("checksum")
                if not isinstance(logged, dict) or "h" not in logged:
                    raise ReplayRejected(f"wait seq={seq} has no checksum")
                n = end - ticks
                if n <= 0:
                    raise ReplayRejected(f"wait seq={seq} does not advance time")
                if cap_ticks is not None and end > cap_ticks:
                    raise ReplayRejected(f"wait seq={seq} ends at tick {end}, past the horizon (tick {cap_ticks})")
                if max_ticks is not None and ticks + n > max_ticks:
                    n = max_ticks - ticks
                world.step(ticks=n)
                result.waits += 1
                st = world.state()
                ticks, date = int(st["ticks"]), int(st["date"])
                if ticks == end:
                    got = world.call("checksum")
                    result.checkpoints.append(("wait", seq, got.get("h")))
                    if got.get("h") != logged.get("h"):
                        result.divergences.append({"seq": seq, "what": "checksum", "live": logged, "replay": got,
                                                   "key": ("wait", seq), "stop": ticks})
                    if on_wait is not None:
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
