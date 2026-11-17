"""Replay of a merged v3 log from the sealed multi-company start (docs/WAYBILL.md, section 5).

The replay checks every record with :class:`railhead.multi.log.LogState` before it applies it, then:

* ``act``: binds the record's company and re-issues the action with its recorded arguments. A
  different outcome (``ok``) is a divergence. With a region map, a build outside the company's regions
  in force at that tick rejects the log (the coordinator never writes one).
* ``meta``: sends the v3 digest (company, decision, arguments) to the GameScript, as the live world did.
* ``wait_req``: no effect on the world (checked for structure only).
* ``step``: runs exactly ``end_ticks - ticks`` ticks and compares the checksum. A difference is a
  divergence.

The header ticks must be the start tick (else ``ReplayRejected``); a different header checksum is a
divergence. The result has the net worth of each company of the header at the end of the replay.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

from railhead.actionlog import read_log
from railhead.bridge import BridgeError
from railhead.exchange import ExchangeError
from railhead.multi.log import FORCED_REASONS, TICKS_PER_DAY, LogRejected, LogState, meta_digest_v3
from railhead.multi.market import Market, MarketConfig, check_system_record
from railhead.multi.regions import FranchiseError, RegionMap
from railhead.multi.world import SharedWorld, WorldProbe
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
    market: Market | None = None
    pending_sys: list[dict] = field(default_factory=list)  # driver records not yet in the log (a torn end)


def horizon_tick(start_ticks: int, horizon_days: int | None) -> int | None:
    return None if horizon_days is None else start_ticks + int(horizon_days) * TICKS_PER_DAY


def _ex_waiters(waiting: dict[int, dict]) -> dict[int, tuple[list[str], int]]:
    return {c: (list(w["until"]), int(w.get("ex_seq", 0))) for c, w in waiting.items()}


def _check_market_step(market: Market, r: dict, waiting: dict[int, dict], now: int) -> None:
    """Before the step: it must not pass a tick at which the market stops the world."""
    cap = market.cap(now, _ex_waiters(waiting))
    if cap is not None and r["end_ticks"] > cap:
        raise LogRejected(f"step seq={r['seq']} ends at {r['end_ticks']}, after the market stop at tick {cap}")


def _market_after_step(market: Market, sw: SharedWorld, r: dict, waiting: dict[int, dict]) -> list[dict]:
    """After the step: sample the monitors, check the market wake reasons of the step record against the
    exchange, then run the driver for this tick (its records must come next in the log)."""
    now = sw.ticks
    if market.book.active:
        market.book.sample(now, sw.monitor_rows())
    allowed = market.wakes(now, _ex_waiters(waiting))
    forced = market.wakes(now, {c: ([], int(w.get("ex_seq", 0))) for c, w in waiting.items()})
    woke = {int(w["company"]): w["reason"] for w in r["wakers"]}
    at_end = {c for c, w in waiting.items() if w["target"] <= now}
    for c, reason in woke.items():
        if c in at_end or reason in ("elapsed", "horizon"):
            continue
        gs_kinds = {u.partition(":")[0] for u in waiting[c]["until"]} - {"deadline", "offer_received",
                                                                       "message_received"}
        if reason in gs_kinds:
            continue
        if reason not in allowed.get(c, set()):
            raise LogRejected(f"step seq={r['seq']}: company {c} cannot wake with reason {reason!r} at tick {now}")
    for c in forced:
        if c not in woke:
            raise LogRejected(f"step seq={r['seq']}: company {c} must wake ({sorted(forced[c])}) at tick {now}")
    assert set(FORCED_REASONS) >= {x for v in forced.values() for x in v}
    out = market.tick_records(now, sw.industries)
    market.book.ensure(market.ex, now, sw.monitor_add)
    return out


def replay_multi(start_save: Path, log_path: Path, workdir: Path, allowed: set[str] | None = None,
                 keep_open: bool = False, max_ticks: int | None = None,
                 horizon_days: int | None = None, regions: RegionMap | None = None,
                 market_cfg: MarketConfig | None = None) -> MultiReplayResult:
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
        market = None
        queue: list[dict] = []  # system records that the driver made and the log must give next, in order
        if market_cfg is not None:
            market = Market(market_cfg, res.companies, start, state.horizon_tick, regions)
            queue.append(copy.deepcopy(market.ex.records[0]))
            queue += market.tick_records(start, sw.industries)
            market.book.ensure(market.ex, start, sw.monitor_add)
        res.market = market
        for r in records[1:]:
            if max_ticks is not None and sw.ticks >= max_ticks:
                break
            waiting_before = copy.deepcopy(state.waiting)
            state.check(r)  # LogRejected is a ReplayRejected
            seq, kind = r["seq"], r["kind"]
            if kind == "ex" and market is None:
                raise LogRejected(f"ex seq={seq}: this task has no market")
            if queue and not (kind == "ex" and r["company"] is None):
                raise LogRejected(f"seq={seq}: the system record {queue[0].get('op')} of tick {sw.ticks} is missing")
            if kind == "ex" and r["company"] is None:
                if not queue:
                    raise LogRejected(f"ex seq={seq}: a system record that the market did not make")
                why = check_system_record(r["x"], queue.pop(0))
                if why:
                    raise LogRejected(f"ex seq={seq}: {why}")
            elif kind == "ex":
                assert market is not None
                try:
                    market.ex.apply(r["x"])
                except ExchangeError as e:
                    raise LogRejected(f"ex seq={seq}: the exchange refuses the record: {e}") from None
                world.call_args("note", {"d": meta_digest_v3(r["company"], "ex", r["x"])})
                market.book.ensure(market.ex, sw.ticks, sw.monitor_add)
            elif kind == "wait_req" and market is not None:
                state.waiting[r["company"]]["ex_seq"] = len(market.ex.records)
            if kind == "act":
                m = r["m"]
                if allowed is None or m not in allowed:
                    raise ReplayRejected(f"command {m!r} is not a player action")
                if regions is not None:
                    slot = int(state.companies[r["company"]]["slot"])
                    try:
                        lic = market.licence_rects(r["company"], r["ticks"]) if market is not None else None
                        regions.check_action(slot, r["ticks"], m, r.get("a") or {}, WorldProbe(sw, r["company"]),
                                             lic)
                    except FranchiseError as e:
                        raise LogRejected(f"act seq={seq}: {e}") from None
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
                if market is not None:
                    _check_market_step(market, r, waiting_before, sw.ticks)
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
                    if market is not None:
                        queue += _market_after_step(market, sw, r, waiting_before)
        res.pending_sys = queue
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
