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

    python -m railhead.multi.coordinator <task_dir> <state_dir>
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
from railhead.actions import check_until, validate
from railhead.bridge import BridgeError
from railhead.client import agent_socket_path, control_socket_path
from railhead.daemon import META_METHODS, READ_METHODS, OpError, _drop_torn_tail, check_meta
from railhead.multi.barrier import DEFAULT_CHUNK_DAYS, Barrier, Waiter
from railhead.multi.log import AUTO_WAIT_DAYS, TICKS_PER_DAY, LogRejected, LogState, wait_args, wait_target
from railhead.multi.replay import horizon_tick, replay_multi
from railhead.multi.world import SharedWorld
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
                               horizon_days=self.spec.horizon_days)
            assert res.world is not None
            self._start_ticks, self._start_date = res.start_ticks, res.start_date
            if res.divergences:
                res.world.close()
                raise RuntimeError(f"the log does not replay: {res.divergences[0]}")
            res.world.attach_log(ActionLog(self.log_path), header=False)
            return res.world, res.state
        sw = SharedWorld(World.load(wdir, start), self.companies)
        self._start_ticks, self._start_date = sw.ticks, sw.date
        sw.attach_log(ActionLog(self.log_path))
        return sw, None

    def _rebuild_barrier(self, sw: SharedWorld, state: LogState | None) -> None:
        old = self.barrier
        submitted = set(state.submitted) if state is not None else set()
        b = Barrier([int(c["id"]) for c in self.companies if int(c["id"]) not in submitted],
                    horizon_tick=self.horizon_tick, chunk_ticks=self.chunk_ticks)
        if old is not None:
            b.turn_start.update({c: t for c, t in old.turn_start.items() if c in b.live})
        if state is not None:
            for c, w in state.waiting.items():
                b.add(self._waiter(sw, c, w["target"], w["until"], w["ticks"], w["auto"]))
        with self.cv:
            self.barrier = b
            self.submitted = submitted
            self.cv.notify_all()

    def _waiter(self, sw: SharedWorld, c: int, target: int, until: list[str], start: int, auto: bool) -> Waiter:
        kinds = {u.partition(":")[0] for u in until}
        vehicles = frozenset(int(v["id"]) for v in sw.query(c, "vehicles")) if "vehicle_crashed" in kinds \
            else frozenset()
        stations = frozenset(int(s["id"]) for s in sw.query(c, "stations")) if "station_first_vehicle" in kinds \
            else frozenset()
        return Waiter(c, target=target, until=list(until), start=start, month=sw.month, vehicles=vehicles,
                      stations=stations, auto=auto)

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
                return self._logged(self.sw.query, c, m, self._obj(a))
        if op == "act":
            m = req.get("m")
            if m not in self.whitelist:
                raise OpError("ERR_NOT_A_PLAYER_ACTION", f"{m!r} is not a player action")
            args = validate(m, self._obj(a), map_size_x=self.sw.world.admin.welcome.map_x)
            return self._play(c, "act", lambda: self._logged(self.sw.act, c, m, args))
        if op == "meta":
            m = req.get("m")
            if m not in META_METHODS:
                raise OpError("ERR_BAD_ARG", f"unknown meta decision {m!r}")
            check_meta(m, self._obj(a))
            return self._play(c, "meta", lambda: self._meta(c, m, a))
        if op == "wait":
            return self._wait(c, req)
        if op == "history":
            n = int(req.get("n", 30))
            with self.world_lock:  # no record is half written while the lock is held
                recs = [r.raw for r in read_log(self.log_path)
                        if r.kind == "step" or (r.kind != "header" and r.raw.get("company") == c)]
            return recs[-n:]
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
        }

    def _wait(self, c: int, req: dict) -> dict:
        unknown = set(req) - {"op", "days", "ticks", "until"}
        if unknown:
            raise OpError("ERR_BAD_ARG", f"wait: unknown argument(s) {sorted(unknown)} (arguments: days, ticks, until)")
        a: dict[str, Any] = {}
        if req.get("ticks") is not None:
            a["ticks"] = req["ticks"]
        else:
            a["days"] = req.get("days", 1)
        until = check_until(req.get("until"))
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
            n = self.barrier.next_ticks(self.sw.ticks)
        sw = self.sw
        t0, obs = self._logged(sw.advance, n)
        out = {"ymd": sw.ymd, "date": sw.date, "ticks": sw.ticks, "paused": True}
        # One section (cv is reentrant): a waiter must never see itself awake without its result, and a
        # failed step record rebuilds the barrier from the log before any waiter sees the wake.
        with self.cv:
            starts = {c: w.start for c, w in self.barrier.waiting.items()}
            wakers = self.barrier.wake(sw.ticks, obs)
            self._logged(sw.log_step, t0, wakers)
            for c, reason in wakers:
                self.results[c] = out | {"reason": reason, "days": (sw.ticks - starts[c]) // TICKS_PER_DAY}
            self.cv.notify_all()

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


def serve(task_dir: Path, state: Path) -> None:
    coord = Coordinator(Path(task_dir), Path(state).resolve()).start()
    signal.signal(signal.SIGTERM, lambda *_: coord.stop.set())
    try:
        while not coord.stop.wait(0.2):
            pass
    finally:
        coord.close()


if __name__ == "__main__":
    if len(sys.argv) == 3:
        serve(Path(sys.argv[1]), Path(sys.argv[2]))
    else:
        raise SystemExit("usage: python -m railhead.multi.coordinator <task_dir> <state_dir>")
