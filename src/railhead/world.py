"""World: one running game the model plays — server process + admin link + GS bridge + bound company.

Every World is either *created* (new map from a seed; the model's company is founded via
``start_ai RailheadIdle``) or *loaded* from a savegame. Episodes, replays and forks always start by
loading the same savegame (fork-fidelity rule, docs/research/03-openttd-source.md §4.5).
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from railhead.actionlog import LOG_VERSION, ActionLog, meta_digest
from railhead.bridge import Bridge, BridgeError
from railhead.server import OpenTTDServer, ServerConfig

AGENT_AI = "RailheadIdle"


class WorldFault(RuntimeError):
    """The engine did not answer a world-changing request: it is unknown whether the change happened.
    The live world can now differ from the action log; rebuild it from the log before more play."""


class World:
    def __init__(self, server: OpenTTDServer, speed: int = 0) -> None:
        self.server = server
        self.admin = server.admin()
        self.bridge = Bridge(self.admin)
        self.bridge.wait_ready(timeout=60)
        self.set_speed(speed)
        self.log: ActionLog | None = None
        self.log_version = LOG_VERSION

    # ------------------------------------------------------------------ construction
    @classmethod
    def create(cls, workdir: Path, config: ServerConfig, speed: int = 0, found_company: bool = True) -> World:
        server = OpenTTDServer(Path(workdir), config).start()
        try:
            w = cls(server, speed=speed)
            if found_company:
                w._found_company()
            return w
        except BaseException:
            server.stop()
            raise

    @classmethod
    def load(cls, workdir: Path, savegame: Path, config: ServerConfig | None = None, speed: int = 0) -> World:
        cfg = config or ServerConfig()
        cfg.savegame = Path(savegame).resolve()
        server = OpenTTDServer(Path(workdir), cfg).start()
        try:
            return cls(server, speed=speed)
        except BaseException:
            server.stop()
            raise

    def _found_company(self, timeout: float = 30.0) -> int:
        existing = {c["id"] for c in self.call("companies")}
        self.admin.rcon(f"start_ai {AGENT_AI}")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            new = [c["id"] for c in self.call("companies") if c["id"] not in existing]
            if new:
                self.call("bind_company", company=new[0])
                return new[0]
            time.sleep(0.02)
        raise TimeoutError("company was not founded (start_ai RailheadIdle)")

    # ------------------------------------------------------------------ api
    def set_speed(self, pct: int) -> None:
        """0 = unlimited (patched engine). Must be re-sent after every load (engine resets it)."""
        out = self.admin.rcon(f"gamespeed {pct}")
        if not out or not out[0].startswith("Game speed"):
            raise RuntimeError(f"engine lacks the Railhead gamespeed patch: {out!r}")

    def call(self, method: str, timeout: float = 60.0, **args: Any) -> Any:
        """For trusted code only. Player or log arguments go through :meth:`call_args`."""
        return self.bridge.request(method, args, timeout=timeout)

    def call_args(self, method: str, args: dict[str, Any] | None = None, timeout: float = 60.0) -> Any:
        """Send ``args`` as one dict: an argument named ``timeout`` cannot change the wait."""
        return self.bridge.request(method, args, timeout=timeout)

    def state(self) -> dict:
        return self.call("state")

    @property
    def company(self) -> int:
        return self.state()["company"]

    def step(
        self, *, days: int | None = None, ticks: int | None = None, until: list[str] | None = None,
        timeout: float | None = None,
    ) -> dict:
        """Advance game time (unlogged). ``until`` = wake conditions, e.g. ["vehicle_crashed", "cash_below:5000"].

        The default timeout grows with the length of the step (600 s + 2 s per game day), so a long
        honest wait on a slow machine does not time out half way."""
        if (days is None) == (ticks is None):
            raise ValueError("step needs exactly one of days= or ticks=")
        args: dict[str, Any] = {"days": days} if days is not None else {"ticks": ticks}
        if until:
            args["until"] = list(until)
        if timeout is None:
            timeout = 600.0 + 2.0 * (days if days is not None else (ticks or 0) / 74)
        return self.call_args("step", args, timeout=timeout)

    # ------------------------------------------------------------------ logged play
    def attach_log(self, log: ActionLog, header: bool = True, version: int = LOG_VERSION) -> None:
        """Record every subsequent act()/wait() — the log + start savegame is the proof of play.
        ``version`` is the version of an existing log that is continued (``header=False``)."""
        self.log = log
        self.log_version = version if not header else LOG_VERSION
        if header:
            st = self.state()
            log.write({"kind": "header", "v": LOG_VERSION, "company": st["company"], "ticks": st["ticks"],
                       "checksum": self.call("checksum")})

    def act(self, method: str, /, **args: Any) -> Any:
        """Issue one player action (GS method) and log it; failures are logged then re-raised.

        A GameScript refusal (``BridgeError``) changed nothing that a replay does not repeat, so it is
        logged. Any other failure (time-out, lost connection, log write) leaves it unknown whether the
        engine applied the action: it raises :class:`WorldFault`, and the owner must rebuild the world
        from the log (the daemon does), so the live world never differs from the log."""
        try:
            ticks = self.state()["ticks"] if self.log else 0
            try:
                ret = self.call_args(method, args)
            except BridgeError as e:
                if self.log:
                    self.log.write({"kind": "act", "ticks": ticks, "m": method, "a": args, "ok": False, "code": e.code})
                raise
            if self.log:
                self.log.write({"kind": "act", "ticks": ticks, "m": method, "a": args, "ok": True, "ret": ret})
            return ret
        except BridgeError:
            raise
        except Exception as e:
            raise WorldFault(f"{method}: {type(e).__name__}: {e}") from e

    def wait(self, *, days: int | None = None, ticks: int | None = None, until: list[str] | None = None) -> dict:
        try:
            t0 = self.state()["ticks"] if self.log else 0
            out = self.step(days=days, ticks=ticks, until=until)
        except BridgeError:
            raise  # the GameScript refused the step: no time passed
        except Exception as e:
            raise WorldFault(f"wait: {type(e).__name__}: {e}") from e
        if self.log:
            try:  # time has passed: from here every failure must leave the log behind the world
                a: dict[str, Any] = {"days": days} if days is not None else {"ticks": ticks}
                if until:
                    a["until"] = list(until)
                self.log.write({"kind": "wait", "ticks": t0, "end_ticks": out["ticks"], "a": a,
                                "reason": out.get("reason"), "checksum": self.call("checksum"),
                                "snap": self.snapshot()})
            except Exception as e:
                raise WorldFault(f"wait record: {type(e).__name__}: {e}") from e
        return out

    def snapshot(self) -> dict:
        """Company KPIs at this instant (for growth curves; ignored by replay)."""
        try:
            c = self.call("company")
            q = (c.get("quarters") or [{}])[0]
            return {"date": self.state()["ymd"], "money": c.get("money"), "loan": c.get("loan"),
                    "net_worth": int(c.get("money") or 0) - int(c.get("loan") or 0),
                    "value": q.get("value"), "income_q": q.get("income"), "expenses_q": q.get("expenses"),
                    "vehicles": len(self.call("vehicles")), "stations": len(self.call("stations"))}
        except BridgeError:
            return {}

    def note(self, method: str, /, **args: Any) -> None:
        """Log a meta decision (forecast, promise, abstain, flag, submit). It changes no game state, but
        in a v2 log its digest goes to the GameScript first, so later wait checksums bind it."""
        if not self.log:
            return
        try:
            ticks = self.state()["ticks"]
            if self.log_version >= 2:
                self.call_args("note", {"d": meta_digest(method, args)})
            self.log.write({"kind": "meta", "ticks": ticks, "m": method, "a": args})
        except Exception as e:
            raise WorldFault(f"note {method}: {type(e).__name__}: {e}") from e

    # ------------------------------------------------------------------ forks
    def fork(self, workdir: Path, reseed: int | None = None) -> World:
        """A new World loaded from a snapshot of this one. ``reseed`` resamples hidden randomness
        (industry production, breakdowns, town growth) so the fork shows *a* future, not *the* future."""
        workdir = Path(workdir)
        snap = self.save(workdir.parent / f"{workdir.name}.fork.sav")
        child = World.load(workdir, snap)
        if reseed is not None:
            out = child.admin.rcon(f"reseed {int(reseed) & 0xFFFFFFFF}")
            if not out or not out[0].startswith("Reseeded"):
                child.close()
                raise RuntimeError(f"engine lacks the Railhead reseed patch: {out!r}")
        return child

    def save(self, dest: Path) -> Path:
        """Synchronous savegame snapshot (threaded_saves=false) copied to ``dest``."""
        name = f"rh_{time.monotonic_ns()}"
        out = self.admin.rcon(f"save {name}")
        src = self.server.save_dir / f"{name}.sav"
        if not src.exists():
            raise RuntimeError(f"save failed: {out!r}")
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), dest)
        return dest

    def close(self) -> None:
        try:
            self.admin.close()
        finally:
            self.server.stop()

    def __enter__(self) -> World:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
