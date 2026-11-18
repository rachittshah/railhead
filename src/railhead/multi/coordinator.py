"""The Waybill coordinator: one process owns one shared World; each agent has its own unix socket
(docs/WAYBILL.md, section 4).

* ``run/agent-<slot>.sock`` serves the agent of company slot ``<slot>``: every request on it is for that
  company. The operations are those of ``railheadd`` (info, query, act, meta, wait, history) without
  forks and without shutdown. ``run/control.sock`` serves ``ping``, ``status`` and ``shutdown``.
* One world lock serialises every engine call (the bridge has one reply queue).
* ``wait`` is a barrier. The request writes a ``wait_req`` record, registers a waiter and then blocks on a
  condition variable, without the world lock. The clock thread steps the world when every live company
  waits (:class:`railhead.multi.barrier.Barrier`), writes one ``step`` record per step with the exact
  tick count, and wakes the companies that the step makes due.
* Stragglers: a company whose turn (the time since its last wake) is longer than ``turn_budget_s`` gets an
  automatic ``wait days=7``, logged as ``wait_req`` with ``auto=true``. Its next act, meta or wait request
  blocks until that wait ends; a wait request from a company that already waits joins that wait.
* Boot and faults: the coordinator loads ``start.sav``; if ``run/actions.jsonl`` exists it replays it
  (and rebuilds the barrier from it). After a ``WorldFault`` it rebuilds the world from the log in the
  same way and replies ``ERR_INFRA``.

    python -m railhead.multi.coordinator <task_dir> <state_dir> [--turn-s SECONDS]
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socketserver
import sys
import threading
from pathlib import Path
from typing import Any

from railhead.actionlog import ActionLog, read_log
from railhead.actions import ActionArgError, validate
from railhead.bridge import BridgeError
from railhead.client import agent_socket_path, control_socket_path
from railhead.daemon import META_METHODS, READ_METHODS, OpError, _drop_torn_tail, check_meta
from railhead.multi.barrier import DEFAULT_CHUNK_DAYS, Barrier, Waiter
from railhead.multi.log import (
    AUTO_WAIT_DAYS,
    EXCHANGE_UNTIL,
    TICKS_PER_DAY,
    LogRejected,
    LogState,
    check_until_v3,
    wait_args,
    wait_target,
)
from railhead.multi.market import (
    COMMANDS,
    QUERIES,
    CommandError,
    ExchangeError,
    Market,
    MarketConfig,
    ex_id,
    parse_command,
    pick_reason,
)
from railhead.multi.regions import FranchiseError, RegionMap
from railhead.multi.replay import horizon_tick, replay_multi
from railhead.multi.world import SharedWorld, WorldProbe
from railhead.replay import default_whitelist
from railhead.task import TaskSpec
from railhead.world import World, WorldFault

DEFAULT_TURN_BUDGET_S = 600.0
WATCH_KINDS = {"vehicle_crashed", "station_first_vehicle"}


class Coordinator:
    def __init__(self, task_dir: Path, state: Path, turn_budget_s: float | None = None,
                 chunk_days: int = DEFAULT_CHUNK_DAYS, auto_wait_days: int = AUTO_WAIT_DAYS) -> None:
        self.task_dir = Path(task_dir)
        self.state = Path(state)
        self.spec = TaskSpec.load(self.task_dir)
        self.companies: list[dict] = list(self.spec.objective["companies"])
        self.by_slot = {int(c["slot"]): int(c["id"]) for c in self.companies}
        self.slot_of = {cid: slot for slot, cid in self.by_slot.items()}
        self.regions = RegionMap.for_task(self.task_dir)
        self.market_cfg = MarketConfig.from_objective(self.spec.objective)
        self.market: Market | None = None
        budget = self.spec.limits.get("agent_turn_s", DEFAULT_TURN_BUDGET_S)
        self.turn_budget_s = turn_budget_s if turn_budget_s is not None else budget
        self.chunk_ticks = int(chunk_days) * TICKS_PER_DAY
        self.auto_wait_days = int(auto_wait_days)
        self.whitelist = default_whitelist() or set()
        self.run_dir = self.state / "run"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.run_dir / "actions.jsonl"
        self.world_lock = threading.Lock()  # every engine call and every log write
        self.cv = threading.Condition(threading.RLock())  # barrier state; reentrant; order: world_lock, then cv
        self.stop = threading.Event()
        self.results: dict[int, dict] = {}
        self.submitted: set[int] = set()
        self.barrier: Barrier | None = None
        self.sw: SharedWorld = self._boot()
        self._threads: list[threading.Thread] = []
        self._servers: list[socketserver.BaseServer] = []

    # ------------------------------------------------------------------ boot / recovery
    def _boot(self) -> SharedWorld:
        meta_path = self.run_dir / "meta.json"
        sw, state = self._load()
        if meta_path.exists():
            self.meta = json.loads(meta_path.read_text())
        else:
            self.meta = {"task_id": self.spec.task_id, "start_ticks": self._start_ticks,
                         "start_date": self._start_date}
            meta_path.write_text(json.dumps(self.meta))
        self.horizon_tick = horizon_tick(int(self.meta["start_ticks"]), self.spec.horizon_days)
        self._rebuild_barrier(sw, state)
        return sw

    def _load(self) -> tuple[SharedWorld, LogState | None]:
        start = self.task_dir / "start.sav"
        wdir = self.state / "world"
        shutil.rmtree(wdir, ignore_errors=True)
        _drop_torn_tail(self.log_path)
        if self.log_path.exists() and self.log_path.stat().st_size > 0:
            res = replay_multi(start, self.log_path, wdir, allowed=self.whitelist, keep_open=True,
                               horizon_days=self.spec.horizon_days, regions=self.regions,
                               market_cfg=self.market_cfg)
            assert res.world is not None
            self._start_ticks, self._start_date = res.start_ticks, res.start_date
            if res.divergences:
                res.world.close()
                raise RuntimeError(f"the log does not replay: {res.divergences[0]}")
            res.world.attach_log(ActionLog(self.log_path), header=False)
            self.market = res.market
            for x in res.pending_sys:  # system records that the driver made but the log did not get
                res.world.ex_record(None, x)
            return res.world, res.state
        sw = SharedWorld(World.load(wdir, start), self.companies)
        self._start_ticks, self._start_date = sw.ticks, sw.date
        sw.attach_log(ActionLog(self.log_path))
        self._start_market(sw)
        return sw, None

    def _start_market(self, sw: SharedWorld) -> None:
        """A new log: the market's init record and the records of the start tick (quarter 0)."""
        if self.market_cfg is None:
            return
        ht = horizon_tick(sw.ticks, self.spec.horizon_days)
        self.market = Market(self.market_cfg, self.companies, sw.ticks, ht, self.regions)
        sw.ex_record(None, self.market.ex.records[0])
        self._market_tick(sw)

    def _market_tick(self, sw: SharedWorld) -> None:
        """World lock held, after a step record (or at boot): the system records of this tick, then the
        monitors that newly signed contracts need. The replay runs the same driver."""
        m = self.market
        assert m is not None
        for x in m.tick_records(sw.ticks, sw.industries):
            sw.ex_record(None, x)
        m.book.ensure(m.ex, sw.ticks, sw.monitor_add)

    def _rebuild_barrier(self, sw: SharedWorld, state: LogState | None) -> None:
        old = self.barrier
        submitted = set(state.submitted) if state is not None else set()
        b = Barrier([int(c["id"]) for c in self.companies if int(c["id"]) not in submitted],
                    horizon_tick=self.horizon_tick, chunk_ticks=self.chunk_ticks)
        if old is not None:
            b.turn_start.update({c: t for c, t in old.turn_start.items() if c in b.live})
        if state is not None:
            for c, w in state.waiting.items():
                b.add(self._waiter(sw, c, w["target"], w["until"], w["ticks"], w["auto"], w.get("ex_seq", 0)))
        with self.cv:
            self.barrier = b
            self.submitted = submitted
            self.cv.notify_all()

    def _waiter(self, sw: SharedWorld, c: int, target: int, until: list[str], start: int, auto: bool,
                ex_seq: int | None = None) -> Waiter:
        kinds = {u.partition(":")[0] for u in until}
        vehicles = frozenset(int(v["id"]) for v in sw.query(c, "vehicles")) if "vehicle_crashed" in kinds \
            else frozenset()
        stations = frozenset(int(s["id"]) for s in sw.query(c, "stations")) if "station_first_vehicle" in kinds \
            else frozenset()
        if ex_seq is None:
            ex_seq = len(self.market.ex.records) if self.market is not None else 0
        return Waiter(c, target=target, until=list(until), start=start, month=sw.month, vehicles=vehicles,
                      stations=stations, auto=auto, ex_seq=ex_seq)

    def _rebuild(self, fault: Exception) -> OpError:
        """After a WorldFault (world lock held): drop the live world and replay the log again."""
        old = self.sw
        with contextlib.suppress(Exception):
            old.close()
        try:
            sw, state = self._load()
            self.sw = sw
            self._rebuild_barrier(sw, state)
        except Exception as e:  # noqa: BLE001
            self.stop.set()
            return OpError("ERR_INFRA", f"the engine failed ({fault}) and the rebuild from the log failed "
                           f"({type(e).__name__}: {e}); the coordinator stops")
        return OpError("ERR_INFRA", f"the engine failed ({fault}); the world was rebuilt from the action log "
                       "and the last request did not happen")

    def _logged(self, fn, *args) -> Any:
        try:
            return fn(*args)
        except WorldFault as e:
            raise self._rebuild(e) from e

    # ------------------------------------------------------------------ turns
    def company(self, slot: int) -> int:
        return self.by_slot[slot]

    def _await_turn(self, c: int) -> None:
        with self.cv:
            while c in self.barrier.waiting and not self.stop.is_set():
                self.cv.wait(0.5)
        if self.stop.is_set():
            raise OpError("ERR_STOPPING", "the coordinator is stopping")

    def _is_waiting(self, c: int) -> bool:
        with self.cv:
            return c in self.barrier.waiting

    def _can_play(self, c: int, what: str) -> None:
        if c in self.submitted:
            raise OpError("ERR_SUBMITTED", "you already submitted; the world now runs without you")
        if what != "meta" and self.horizon_tick is not None and self.sw.ticks >= self.horizon_tick:
            raise OpError("ERR_HORIZON", "the task horizon has been reached; no more actions")

    def days_left(self) -> int:
        if self.horizon_tick is None:
            return 0
        return max(0, -(-(self.horizon_tick - self.sw.ticks) // TICKS_PER_DAY))

    # ------------------------------------------------------------------ operations
    def handle(self, slot: int, req: dict) -> Any:
        op = req.get("op")
        a = req.get("a") or {}
        c = self.company(slot)
        if op == "ping":
            return {"pong": True, "task_id": self.spec.task_id, "pid": os.getpid(), "company": c, "slot": slot}
        if op == "info":
            with self.world_lock:
                return self.info(c)
        if op == "query":
            m = req.get("m")
            if m not in READ_METHODS:
                raise OpError("ERR_NOT_A_QUERY", f"{m!r} is not a read-only query")
            with self.world_lock:
                rows = self._logged(self.sw.query, c, m, self._obj(a))
            if m == "monitor_read":  # other companies' monitors would show their contracts
                rows = [r for r in rows if int(r.get("company", -1)) == c]
            return rows
        if op == "act":
            m = req.get("m")
            if m not in self.whitelist:
                raise OpError("ERR_NOT_A_PLAYER_ACTION", f"{m!r} is not a player action")
            args = validate(m, self._obj(a), map_size_x=self.sw.world.admin.welcome.map_x)
            return self._play(c, "act", lambda: self._act(c, m, args))
        if op == "meta":
            m = req.get("m")
            if m not in META_METHODS:
                raise OpError("ERR_BAD_ARG", f"unknown meta decision {m!r}")
            check_meta(m, self._obj(a))
            return self._play(c, "meta", lambda: self._meta(c, m, a))
        if op == "wait":
            return self._wait(c, req)
        if op == "ex":
            return self._ex(c, req.get("m"), self._obj(a))
        if op == "history":
            n = int(req.get("n", 30))
            with self.world_lock:  # no record is half written while the lock is held
                recs = [r.raw for r in read_log(self.log_path)
                        if r.kind == "step" or (r.kind != "header" and r.raw.get("company") == c)]
            # a step shows only this company's wake: the wake reasons of others can reveal their market
            return [dict(r, wakers=[w for w in r["wakers"] if w.get("company") == c], snap={})
                    if r.get("kind") == "step" else r for r in recs[-n:]]
        if op.startswith("fork_"):
            raise OpError("ERR_NOT_SUPPORTED", "forks are not available in a shared world")
        raise OpError("ERR_UNKNOWN_OP", f"unknown op {op!r}")

    @staticmethod
    def _obj(a: Any) -> dict:
        if not isinstance(a, dict):
            raise OpError("ERR_BAD_ARG", "arguments must be an object")
        return a

    def _play(self, c: int, what: str, fn) -> Any:
        """Run ``fn`` under the world lock when it is the turn of ``c``. The checks run again under the
        lock, in the same section as the log write: an automatic wait can start between the two."""
        while True:
            self._await_turn(c)
            with self.world_lock:
                if self._is_waiting(c):
                    continue
                self._can_play(c, what)
                return fn()

    def _act(self, c: int, m: str, args: dict) -> Any:
        """World lock held. The franchise check runs at the current tick, before the engine sees the
        action; a refused build is not logged."""
        if self.regions is not None:
            try:
                lic = self.market.licence_rects(c, self.sw.ticks) if self.market is not None else None
                self._logged(self.regions.check_action, self.slot_of[c], self.sw.ticks, m, args,
                             WorldProbe(self.sw, c), lic)
            except FranchiseError as e:
                raise OpError(e.code, str(e)) from None
        return self._logged(self.sw.act, c, m, args)

    # ------------------------------------------------------------------ the market
    def _ex(self, c: int, m: Any, a: dict) -> Any:
        """``op=ex``: a market query (any time) or a market command (on the company's turn, logged).
        Agents get views and query results only, never raw exchange records."""
        if self.market is None:
            raise OpError("ERR_NO_MARKET", "this task has no market (no contracts, tenders, messages or ledger)")
        me = ex_id(c)
        if m in QUERIES:
            with self.world_lock:
                ex = self.market.ex
                # the exchange's own tick is that of its last record; agents need the world tick
                if m == "view":
                    return ex.public_view(me) | {"tick": self.sw.ticks}
                if m == "market":
                    return ex.market(me) | {"tick": self.sw.ticks, "region": self.region_of(c), "arm": ex.arm,
                                            "licensed": self.market.licence_rects(c, self.sw.ticks),
                                            "obligations": [o for o in ex.obligations_of(me)
                                                            if o["status"] == "open"]}
                return getattr(ex, m)(me)
        if m not in COMMANDS:
            raise OpError("ERR_UNKNOWN_OP", f"unknown market operation {m!r}; queries: {', '.join(QUERIES)}; "
                          f"commands: {', '.join(COMMANDS)}")
        return self._play(c, "act", lambda: self._ex_command(c, m, a))

    def _ex_command(self, c: int, m: str, a: dict) -> dict:
        """World lock held, the company's turn: run the command in the exchange, log it, add monitors."""
        mk, sw, me = self.market, self.sw, ex_id(c)
        assert mk is not None
        ex = mk.ex
        try:
            base = None
            if m in ("counter", "amend") and isinstance(a.get("contract_id"), str):
                cur = ex.contract_map.get(a["contract_id"].strip().upper())
                if cur is not None:
                    base = {k: (me if v is None else v) for k, v in cur.terms.items()}
            kw = parse_command(m, a, me=me, now=sw.ticks, companies=self.companies, base_terms=base)
            if "terms" in kw:
                self._check_terms(c, kw["terms"])
            rec = getattr(ex, m)(sw.ticks, me, **kw)
        except CommandError as e:
            raise OpError(e.code, str(e)) from None
        except ExchangeError as e:
            raise OpError(f"ERR_{e.code.upper()}", e.message if hasattr(e, "message") else str(e)) from None
        self._logged(sw.ex_record, c, rec)
        self._logged(mk.book.ensure, ex, sw.ticks, sw.monitor_add)
        reply: dict[str, Any] = {"recorded": m, "tick": sw.ticks}
        cid = rec.get("contract_id") or rec.get("tender_id")
        if cid in ex.contract_map:
            reply["contract"] = ex._contract_view(ex.contract_map[cid], me)
        if m == "pay":
            reply["ledger_balance"] = mk.balance(c)
        return reply

    def _check_terms(self, c: int, terms: dict) -> None:
        """The engine facts that the exchange cannot check: the cargo and the industry exist, and the
        deadline is not after the horizon (nothing settles after it)."""
        sw = self.sw
        if "cargo" in terms and isinstance(terms["cargo"], int):
            cargos = {int(x["id"]) for x in self._logged(sw.query, c, "cargos")}
            if terms["cargo"] not in cargos:
                raise CommandError(f"cargo {terms['cargo']} does not exist (rh cargos lists them)")
        if "industry" in terms and isinstance(terms["industry"], int):
            inds = {int(x["id"]) for x in self._logged(sw.industries)}
            if terms["industry"] not in inds:
                raise CommandError(f"industry {terms['industry']} does not exist (rh industries lists them)")
        if "rect" in terms and self.regions is not None:
            grantor = terms.get("grantor")
            if grantor is None or int(grantor) not in self.slot_of:
                raise CommandError("a licence needs the grantor (the company whose region the rectangle is in)")
            from railhead.multi.regions import clip

            own = self.regions.rects_at(self.slot_of[int(grantor)], sw.ticks)
            r = terms["rect"]
            inside = sum((x[2] - x[0] + 1) * (x[3] - x[1] + 1) for x in clip(r, own))
            if inside != (r[2] - r[0] + 1) * (r[3] - r[1] + 1):
                shown = "; ".join(f"{x[0]},{x[1]},{x[2]},{x[3]}" for x in own)
                raise CommandError(f"rect {r} is not inside the region of the grantor ({shown})")
        dl = terms.get("deadline")
        if self.horizon_tick is not None and isinstance(dl, int) and dl > self.horizon_tick:
            raise CommandError(f"deadline {dl} is after the end of the game (tick {self.horizon_tick}); "
                               "contracts must settle by then")

    def _meta(self, c: int, m: str, a: dict) -> dict:
        self._logged(self.sw.note, c, m, a)
        if m == "submit":
            with self.cv:
                self.submitted.add(c)
                self.barrier.submit(c)
                self.cv.notify_all()
        return {"recorded": m}

    def info(self, c: int) -> dict:
        name = next(x["name"] for x in self.companies if int(x["id"]) == c)
        return {
            "task_id": self.spec.task_id, "family": self.spec.family, "title": self.spec.title,
            "date": self.sw.ymd, "ticks": self.sw.ticks, "company": c, "company_name": name,
            "companies": self.companies, "days_left": self.days_left(),
            "horizon_days": self.spec.horizon_days, "runoff_days": self.spec.runoff_days,
            "fork_days_left": 0, "forks_alive": [], "max_forks": 0,
            "submitted": c in self.submitted, "objective": {}, "turn_budget_s": self.turn_budget_s,
            "map_x": self.sw.world.admin.welcome.map_x, "map_y": self.sw.world.admin.welcome.map_y,
            "briefing": self.spec.briefing,
            "region": self.region_of(c),
            "market": None if self.market is None else {
                "arm": self.market.cfg.arm, "you": ex_id(c), "ledger_balance": self.market.balance(c),
                "quarter_days": self.market.cfg.quarter_days,
                "companies": {ex_id(x["id"]): x["name"] for x in self.companies}},
        }

    def region_of(self, c: int) -> list[list[int]] | None:
        """The franchise rectangles [x0, y0, x1, y1] of company ``c`` now, or None without regions."""
        return None if self.regions is None else self.regions.rects_at(self.slot_of[c], self.sw.ticks)

    def _wait(self, c: int, req: dict) -> dict:
        unknown = set(req) - {"op", "days", "ticks", "until"}
        if unknown:
            raise OpError("ERR_BAD_ARG", f"wait: unknown argument(s) {sorted(unknown)} (arguments: days, ticks, until)")
        a: dict[str, Any] = {}
        if req.get("ticks") is not None:
            a["ticks"] = req["ticks"]
        else:
            a["days"] = req.get("days", 1)
        try:
            until = check_until_v3(req.get("until"))
        except ActionArgError as e:
            raise OpError("ERR_BAD_ARG", str(e)) from None
        if until and self.market is None and set(until) & EXCHANGE_UNTIL:
            raise OpError("ERR_BAD_ARG", "this task has no market: deadline, offer_received and message_received "
                          "do not apply")
        if until:
            a["until"] = until
        try:
            wait_args(a)
        except LogRejected as e:
            raise OpError("ERR_BAD_ARG", str(e)) from None
        if not self._is_waiting(c):  # a company that already waits joins that wait
            self._play(c, "wait", lambda: self._register(c, a, auto=False))
        with self.cv:
            while c in self.barrier.waiting and not self.stop.is_set():
                self.cv.wait(1.0)
            if self.stop.is_set():
                raise OpError("ERR_STOPPING", "the coordinator is stopping")
            return self.results.get(c) or {}

    def _register(self, c: int, a: dict, auto: bool) -> int:
        """World lock held, ``c`` not waiting: log the wait request and add the waiter atomically with
        respect to the clock thread (which needs the world lock to step)."""
        sw = self.sw
        target = wait_target(sw.ticks, a, self.horizon_tick)
        w = self._logged(self._waiter, sw, c, target, a.get("until") or [], sw.ticks, auto)
        self._logged(sw.wait_req, c, a, target, auto)
        with self.cv:
            self.results.pop(c, None)
            self.barrier.add(w)
            self.cv.notify_all()
        return target

    # ------------------------------------------------------------------ clock and watchdog threads
    def clock_loop(self) -> None:
        while not self.stop.is_set():
            with self.cv:
                while not self.stop.is_set() and not self.barrier.ready(self.sw.ticks):
                    self.cv.wait(0.5)
            if self.stop.is_set():
                return
            with self.world_lock:
                try:
                    self._step_once()
                except OpError:
                    continue
                except Exception:  # noqa: BLE001 - keep the clock alive; the error is in the log file
                    import traceback

                    traceback.print_exc()
                    self.stop.wait(1.0)

    def _step_once(self) -> None:
        with self.cv:
            if not self.barrier.ready(self.sw.ticks):
                return
            cap = self.market.cap(self.sw.ticks, self._ex_waiters()) if self.market is not None else None
            n = self.barrier.next_ticks(self.sw.ticks, cap)
        sw = self.sw
        t0, obs = self._logged(sw.advance, n)
        if self.market is not None:
            if self.market.book.active:
                self.market.book.sample(sw.ticks, self._logged(sw.monitor_rows))
            with self.cv:
                all_market = self.market.wakes(sw.ticks, self._ex_waiters())
                obs.market = {c: pick_reason(r) for c, r in all_market.items()}
        out = {"ymd": sw.ymd, "date": sw.date, "ticks": sw.ticks, "paused": True}
        # One section (cv is reentrant): a waiter must never see itself awake without its result, and a
        # failed step record rebuilds the barrier from the log before any waiter sees the wake.
        with self.cv:
            starts = {c: w.start for c, w in self.barrier.waiting.items()}
            wakers = self.barrier.wake(sw.ticks, obs)
            self._logged(sw.log_step, t0, wakers)
            if self.market is not None:
                self._logged(self._market_tick, sw)
            for c, reason in wakers:
                # the log keeps one reason; the agent gets every reason of this tick (a deadline or the end of
                # the wait must not hide that one of its payments falls due)
                every = sorted({reason} | (all_market.get(c, set()) if self.market is not None else set()))
                self.results[c] = out | {"reason": reason, "reasons": every,
                                         "days": (sw.ticks - starts[c]) // TICKS_PER_DAY}
            self.cv.notify_all()

    def _ex_waiters(self) -> dict[int, tuple[list[str], int]]:
        with self.cv:
            return {c: (list(w.until), w.ex_seq) for c, w in self.barrier.waiting.items()}

    def watchdog_loop(self, period: float = 0.25) -> None:
        while not self.stop.wait(period):
            with self.cv:
                due = self.barrier.overdue(self.turn_budget_s)
            for c in due:
                with self.world_lock:
                    with self.cv:
                        if c not in self.barrier.overdue(self.turn_budget_s):
                            continue
                    if self.horizon_tick is not None and self.sw.ticks >= self.horizon_tick:
                        continue
                    with contextlib.suppress(OpError):
                        self._register(c, {"days": self.auto_wait_days}, auto=True)

    # ------------------------------------------------------------------ sockets
    def start(self) -> Coordinator:
        """Serve the agent sockets and the control socket, and start the clock and watchdog threads."""
        coord = self

        def handler_for(slot: int | None):
            class Handler(socketserver.StreamRequestHandler):
                def handle(self) -> None:
                    line = self.rfile.readline()
                    if not line:
                        return
                    try:
                        req = json.loads(line)
                        if not isinstance(req, dict):
                            raise TypeError("a request is a JSON object")
                        result = coord.control(req) if slot is None else coord.handle(slot, req)
                        reply = {"ok": True, "result": result}
                    except OpError as e:
                        reply = {"ok": False, "code": e.code, "msg": e.msg}
                    except BridgeError as e:
                        reply = {"ok": False, "code": e.code, "msg": e.message}
                    except (TypeError, ValueError, KeyError) as e:
                        reply = {"ok": False, "code": "ERR_BAD_ARG", "msg": str(e)}
                    except Exception as e:  # noqa: BLE001 - never kill the coordinator on one bad request
                        reply = {"ok": False, "code": "ERR_INTERNAL", "msg": f"{type(e).__name__}: {e}"}
                    with contextlib.suppress(OSError):
                        self.wfile.write(json.dumps(reply, default=str).encode() + b"\n")
            return Handler

        class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True
            allow_reuse_address = True

        socks = {}
        for slot in sorted(self.by_slot):
            path = agent_socket_path(self.state, slot)
            path.unlink(missing_ok=True)
            self._servers.append(Server(str(path), handler_for(slot)))
            os.chmod(path, 0o666)
            socks[str(slot)] = str(path)
        cpath = control_socket_path(self.state)
        cpath.unlink(missing_ok=True)
        self._servers.append(Server(str(cpath), handler_for(None)))
        (self.run_dir / "sockets.json").write_text(json.dumps({"agents": socks, "control": str(cpath)}))
        for srv in self._servers:
            self._threads.append(threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1},
                                                  daemon=True))
        self._threads.append(threading.Thread(target=self.clock_loop, daemon=True, name="clock"))
        self._threads.append(threading.Thread(target=self.watchdog_loop, daemon=True, name="watchdog"))
        for t in self._threads:
            t.start()
        return self

    def control(self, req: dict) -> Any:
        op = req.get("op")
        if op == "ping":
            return {"pong": True, "task_id": self.spec.task_id, "pid": os.getpid()}
        if op == "status":
            with self.cv:
                waiting = {str(c): {"target": w.target, "auto": w.auto} for c, w in self.barrier.waiting.items()}
                live = sorted(self.barrier.live)
            return {"ticks": self.sw.ticks, "date": self.sw.ymd, "horizon_tick": self.horizon_tick,
                    "live": live, "waiting": waiting, "submitted": sorted(self.submitted)}
        if op == "snapshot":  # the live end state, to compare with the replay of the log
            with self.world_lock:
                return {"ticks": self.sw.ticks, "date": self.sw.ymd, "checksum": self.sw.world.call("checksum"),
                        "net_worth": {str(c): v for c, v in self.sw.net_worth().items()}}
        if op == "shutdown":
            self.stop.set()
            return {"stopping": True}
        raise OpError("ERR_UNKNOWN_OP", f"unknown op {op!r}")

    def close(self) -> None:
        self.stop.set()
        with self.cv:
            self.cv.notify_all()
        for srv in self._servers:
            srv.shutdown()
            srv.server_close()
        for t in self._threads:
            t.join(timeout=10)
        with self.world_lock:
            self.sw.close()
        for slot in self.by_slot:
            agent_socket_path(self.state, slot).unlink(missing_ok=True)
        control_socket_path(self.state).unlink(missing_ok=True)


def serve(task_dir: Path, state: Path, turn_budget_s: float | None = None) -> None:
    coord = Coordinator(Path(task_dir), Path(state).resolve(), turn_budget_s=turn_budget_s).start()
    signal.signal(signal.SIGTERM, lambda *_: coord.stop.set())
    try:
        while not coord.stop.wait(0.2):
            pass
    finally:
        coord.close()


def main(argv: list[str]) -> int:
    turn_s = None
    if "--turn-s" in argv:
        i = argv.index("--turn-s")
        turn_s, argv = float(argv[i + 1]), argv[:i] + argv[i + 2:]
    if len(argv) != 2:
        print("usage: python -m railhead.multi.coordinator <task_dir> <state_dir> [--turn-s SECONDS]", file=sys.stderr)
        return 2
    serve(Path(argv[0]), Path(argv[1]), turn_s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
