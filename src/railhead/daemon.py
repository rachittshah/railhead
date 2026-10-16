"""railheadd — owns one task's world; the agent reaches it only through a unix socket.

* Boot: load the sealed ``start.sav``; if ``run/actions.jsonl`` already exists (daemon was killed),
  *replay* it to recover the exact state, then keep appending (the log is the source of truth).
* Ops: info · query (read-only GS methods) · act (whitelisted player actions, logged) · wait
  (capped at the task horizon, logged) · fork_* (priced, reseeded sandboxes) · meta (forecast,
  promise, abstain, flag, submit — logged) · history · shutdown.
* ``ensure_up`` is idempotent and race-safe (flock) so ENTRYPOINT, healthcheck and ``rh`` can all call it.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import signal
import socket
import socketserver
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from railhead.actionlog import ActionLog, read_log
from railhead.bridge import BridgeError
from railhead.client import Client, DaemonError, socket_path
from railhead.replay import default_whitelist, replay
from railhead.task import TaskSpec, state_root, task_dir
from railhead.world import World

READ_METHODS = {
    "state", "checksum", "companies", "map_info", "towns", "industries", "cargos", "engines", "stations",
    "vehicles", "orders", "depots", "company", "tile", "tiles", "subsidies", "catchment", "monitor_read",
}
META_METHODS = {"forecast", "promise", "abstain", "flag", "submit"}
TICKS_PER_DAY = 74


class OpError(Exception):
    def __init__(self, code: str, msg: str = "") -> None:
        super().__init__(msg)
        self.code, self.msg = code, msg


class Railheadd:
    def __init__(self, task_id: str, state: Path) -> None:
        self.task_id = task_id
        self.state = Path(state)
        self.tdir = task_dir(task_id)
        self.spec = TaskSpec.load(self.tdir)
        self.whitelist = default_whitelist() or set()
        self.lock = threading.Lock()
        self.forks: dict[str, World] = {}
        self.fork_days_used = 0
        self.forks_created = 0
        self.submitted = False
        self.run_dir = self.state / "run"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.world = self._boot()

    # ------------------------------------------------------------------ boot / recovery
    def _boot(self) -> World:
        start = self.tdir / "start.sav"
        log_path = self.run_dir / "actions.jsonl"
        wdir = self.state / "world"
        shutil.rmtree(wdir, ignore_errors=True)
        shutil.rmtree(self.state / "forks", ignore_errors=True)
        meta_path = self.run_dir / "meta.json"
        recovering = log_path.exists() and log_path.stat().st_size > 0
        if recovering:
            res = replay(start, log_path, wdir, allowed=self.whitelist, keep_open=True)
            world = res.world
            assert world is not None
            for rec in read_log(log_path):
                if rec.kind == "meta" and rec.raw.get("m") == "submit":
                    self.submitted = True
        else:
            world = World.load(wdir, start)
        self._record_child(world)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
        else:
            st = world.state()
            meta = {"task_id": self.task_id, "start_date": st["date"], "start_ticks": st["ticks"],
                    "end_date": st["date"] + self.spec.horizon_days}
            meta_path.write_text(json.dumps(meta))
        self.meta = meta
        world.attach_log(ActionLog(log_path), header=not recovering)
        return world

    def _record_child(self, w: World) -> None:
        pids_path = self.state / "children.json"
        pids = json.loads(pids_path.read_text()) if pids_path.exists() else []
        if w.server.proc:
            pids.append(w.server.proc.pid)
        pids_path.write_text(json.dumps(pids))

    # ------------------------------------------------------------------ helpers
    def days_left(self) -> int:
        return max(0, self.meta["end_date"] - self.world.state()["date"])

    def info(self) -> dict:
        st = self.world.state()
        return {
            "task_id": self.task_id, "family": self.spec.family, "title": self.spec.title,
            "date": st["ymd"], "company": st["company"], "days_left": max(0, self.meta["end_date"] - st["date"]),
            "horizon_days": self.spec.horizon_days, "runoff_days": self.spec.runoff_days,
            "fork_days_left": self.spec.max_fork_days - self.fork_days_used,
            "forks_alive": sorted(self.forks), "max_forks": self.spec.max_forks,
            "submitted": self.submitted, "objective": self.spec.objective,
            "map_x": self.world.admin.welcome.map_x, "map_y": self.world.admin.welcome.map_y,
            "game_port": self.world.server.game_port,
            "briefing": self.spec.briefing,
        }

    def _fork(self, name: str) -> World:
        if name not in self.forks:
            raise OpError("ERR_NO_FORK", f"no fork named {name!r}; create it with fork_create")
        return self.forks[name]

    # ------------------------------------------------------------------ dispatch
    def handle(self, req: dict) -> Any:
        op = req.get("op")
        a = req.get("a") or {}
        if op == "ping":
            return {"pong": True, "task_id": self.task_id, "pid": os.getpid()}
        if op == "info":
            return self.info()
        if op == "query":
            m = req.get("m")
            if m not in READ_METHODS:
                raise OpError("ERR_NOT_A_QUERY", f"{m!r} is not a read-only query")
            return self.world.call(m, **a)
        if op == "act":
            m = req.get("m")
            if m not in self.whitelist:
                raise OpError("ERR_NOT_A_PLAYER_ACTION", f"{m!r} is not a player action")
            if self.submitted:
                raise OpError("ERR_SUBMITTED", "you already submitted; the world now runs without you")
            if self.days_left() <= 0:
                raise OpError("ERR_HORIZON", "the task horizon has been reached; no more actions")
            return self.world.act(m, **a)
        if op == "wait":
            return self._wait(req)
        if op == "meta":
            m = req.get("m")
            if m not in META_METHODS:
                raise OpError("ERR_BAD_ARG", f"unknown meta decision {m!r}")
            self.world.note(m, **a)
            if m == "submit":
                self.submitted = True
            return {"recorded": m}
        if op == "history":
            n = int(req.get("n", 30))
            recs = [r.raw for r in read_log(self.run_dir / "actions.jsonl") if r.kind != "header"]
            return recs[-n:]
        if op.startswith("fork_"):
            return self._fork_op(op, req, a)
        raise OpError("ERR_UNKNOWN_OP", f"unknown op {op!r}")

    def _wait(self, req: dict) -> dict:
        left = self.days_left()
        if left <= 0:
            raise OpError("ERR_HORIZON", "the task horizon has been reached")
        until = req.get("until")
        if req.get("ticks") is not None:
            ticks = max(1, min(int(req["ticks"]), left * TICKS_PER_DAY))
            return self.world.wait(ticks=ticks, until=until)
        days = max(1, min(int(req.get("days", 1)), left))
        return self.world.wait(days=days, until=until)

    def _fork_op(self, op: str, req: dict, a: dict) -> Any:
        name = str(req.get("name", "f"))
        if op == "fork_create":
            if len(self.forks) >= self.spec.max_forks:
                raise OpError("ERR_FORK_LIMIT", f"at most {self.spec.max_forks} live forks; drop one first")
            if name in self.forks:
                raise OpError("ERR_BAD_ARG", f"fork {name!r} exists")
            exact = bool(req.get("exact")) and bool(self.spec.limits.get("allow_exact_forks"))
            self.forks_created += 1
            seed = None if exact else int.from_bytes(
                hashlib.sha256(f"{self.task_id}:{self.forks_created}".encode()).digest()[:4], "little")
            child = self.world.fork(self.state / "forks" / name, reseed=seed)
            self._record_child(child)
            self.forks[name] = child
            return {"fork": name, "exact": exact, "fork_days_left": self.spec.max_fork_days - self.fork_days_used}
        if op == "fork_list":
            return {"forks": sorted(self.forks), "fork_days_left": self.spec.max_fork_days - self.fork_days_used}
        w = self._fork(name)
        if op == "fork_query":
            m = req.get("m")
            if m not in READ_METHODS:
                raise OpError("ERR_NOT_A_QUERY", f"{m!r} is not a read-only query")
            return w.call(m, **a)
        if op == "fork_act":
            m = req.get("m")
            if m not in self.whitelist:
                raise OpError("ERR_NOT_A_PLAYER_ACTION", f"{m!r} is not a player action")
            return w.call(m, **a)
        if op == "fork_wait":
            days = max(1, int(req.get("days", 1)))
            left = self.spec.max_fork_days - self.fork_days_used
            if days > left:
                raise OpError("ERR_FORK_BUDGET", f"only {left} fork-days left in the imagination budget")
            out = w.step(days=days, until=req.get("until"))
            self.fork_days_used += max(1, int(out.get("days", days)))
            out["fork_days_left"] = self.spec.max_fork_days - self.fork_days_used
            return out
        if op == "fork_drop":
            self.forks.pop(name).close()
            return {"dropped": name}
        raise OpError("ERR_UNKNOWN_OP", f"unknown op {op!r}")

    def close(self) -> None:
        for w in self.forks.values():
            w.close()
        self.world.close()


# ---------------------------------------------------------------------- socket server
def serve(task_id: str, state: Path) -> None:
    state.mkdir(parents=True, exist_ok=True)
    d = Railheadd(task_id, state)
    sock = socket_path(state)
    if sock.exists():
        sock.unlink()
    stop = threading.Event()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self) -> None:
            line = self.rfile.readline()
            if not line:
                return
            try:
                req = json.loads(line)
                with d.lock:
                    if req.get("op") == "shutdown":
                        stop.set()
                        reply = {"ok": True, "result": {"stopping": True}}
                    else:
                        reply = {"ok": True, "result": d.handle(req)}
            except OpError as e:
                reply = {"ok": False, "code": e.code, "msg": e.msg}
            except BridgeError as e:
                reply = {"ok": False, "code": e.code, "msg": e.message}
            except (TypeError, ValueError, KeyError) as e:
                reply = {"ok": False, "code": "ERR_BAD_ARG", "msg": str(e)}
            except Exception as e:  # noqa: BLE001 - never kill the daemon on one bad request
                reply = {"ok": False, "code": "ERR_INTERNAL", "msg": f"{type(e).__name__}: {e}"}
            self.wfile.write(json.dumps(reply, default=str).encode() + b"\n")

    class Server(socketserver.UnixStreamServer):
        allow_reuse_address = True

    srv = Server(str(sock), Handler)
    os.chmod(sock, 0o666)
    (state / "daemon.pid").write_text(str(os.getpid()))
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    t.start()
    try:
        while not stop.is_set():
            stop.wait(0.2)
    finally:
        srv.shutdown()
        d.close()
        for p in (sock, state / "daemon.pid"):
            p.unlink(missing_ok=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_stale_children(state: Path) -> None:
    path = state / "children.json"
    if not path.exists():
        return
    for pid in json.loads(path.read_text()):
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    path.unlink()


def ensure_up(task_id: str | None = None, state: Path | None = None, timeout: float = 180.0) -> int:
    """Start railheadd for ``task_id`` unless it already runs. Returns the daemon pid."""
    state = Path(state or state_root())
    state.mkdir(parents=True, exist_ok=True)
    task_id = task_id or os.environ.get("RAILHEAD_TASK")
    with open(state / "up.lock", "w") as lockf:
        fcntl.flock(lockf, fcntl.LOCK_EX)
        pidf = state / "daemon.pid"
        if pidf.exists() and _alive(int(pidf.read_text() or 0)):
            try:
                info = Client(state, timeout=10).request("ping")
                if task_id and info["task_id"] != task_id:
                    raise DaemonError("ERR_TASK_MISMATCH", f"daemon runs {info['task_id']}, not {task_id}")
                return int(info["pid"])
            except (OSError, DaemonError) as e:
                if isinstance(e, DaemonError) and e.code == "ERR_TASK_MISMATCH":
                    raise
        if not task_id:
            raise DaemonError("ERR_NO_TASK", "no task given (set RAILHEAD_TASK)")
        _kill_stale_children(state)
        log = open(state / "daemon.log", "ab")
        proc = subprocess.Popen(
            [sys.executable, "-m", "railhead.daemon", "serve", task_id, str(state)],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
        )
        log.close()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                tail = (state / "daemon.log").read_text(errors="replace")[-3000:]
                raise DaemonError("ERR_DAEMON", f"railheadd exited ({proc.returncode}):\n{tail}")
            try:
                return int(Client(state, timeout=10).request("ping")["pid"])
            except (OSError, DaemonError):
                time.sleep(0.1)
        raise DaemonError("ERR_DAEMON", "railheadd did not come up in time")


def shutdown(state: Path | None = None) -> None:
    state = Path(state or state_root())
    try:
        Client(state, timeout=30).request("shutdown")
    except (OSError, DaemonError):
        pass
    pidf = state / "daemon.pid"
    deadline = time.monotonic() + 15
    while pidf.exists() and time.monotonic() < deadline:
        pid = int(pidf.read_text() or 0)
        if not _alive(pid):
            break
        time.sleep(0.1)
    if pidf.exists():
        pid = int(pidf.read_text() or 0)
        if pid and _alive(pid):
            os.kill(pid, signal.SIGKILL)
        pidf.unlink(missing_ok=True)
    _kill_stale_children(state)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "serve":
        serve(sys.argv[2], Path(sys.argv[3]))
    else:
        raise SystemExit("usage: python -m railhead.daemon serve <task_id> <state_dir>")
