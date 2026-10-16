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

from railhead.actionlog import ActionLog
from railhead.bridge import Bridge, BridgeError
from railhead.server import OpenTTDServer, ServerConfig

AGENT_AI = "RailheadIdle"


class World:
    def __init__(self, server: OpenTTDServer, speed: int = 0) -> None:
        self.server = server
        self.admin = server.admin()
        self.bridge = Bridge(self.admin)
        self.bridge.wait_ready(timeout=60)
        self.set_speed(speed)
        self.log: ActionLog | None = None

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
        return self.bridge.call(method, timeout=timeout, **args)

    def state(self) -> dict:
        return self.call("state")

    @property
    def company(self) -> int:
        return self.state()["company"]

    def step(
        self, *, days: int | None = None, ticks: int | None = None, until: list[str] | None = None,
        timeout: float = 600.0,
    ) -> dict:
        """Advance game time (unlogged). ``until`` = wake conditions, e.g. ["vehicle_crashed", "cash_below:5000"]."""
        if (days is None) == (ticks is None):
            raise ValueError("step needs exactly one of days= or ticks=")
        args: dict[str, Any] = {"days": days} if days is not None else {"ticks": ticks}
        if until:
            args["until"] = list(until)
        return self.call("step", timeout=timeout, **args)

    # ------------------------------------------------------------------ logged play
    def attach_log(self, log: ActionLog, header: bool = True) -> None:
        """Record every subsequent act()/wait() — the log + start savegame is the proof of play."""
        self.log = log
        if header:
            st = self.state()
            log.write({"kind": "header", "v": 1, "company": st["company"], "ticks": st["ticks"],
                       "checksum": self.call("checksum")})

    def act(self, method: str, **args: Any) -> Any:
        """Issue one player action (GS method) and log it; failures are logged then re-raised."""
        ticks = self.state()["ticks"] if self.log else 0
        try:
            ret = self.call(method, **args)
        except BridgeError as e:
            if self.log:
                self.log.write({"kind": "act", "ticks": ticks, "m": method, "a": args, "ok": False, "code": e.code})
            raise
        if self.log:
            self.log.write({"kind": "act", "ticks": ticks, "m": method, "a": args, "ok": True, "ret": ret})
        return ret

    def wait(self, *, days: int | None = None, ticks: int | None = None, until: list[str] | None = None) -> dict:
        t0 = self.state()["ticks"] if self.log else 0
        out = self.step(days=days, ticks=ticks, until=until)
        if self.log:
            a: dict[str, Any] = {"days": days} if days is not None else {"ticks": ticks}
            if until:
                a["until"] = list(until)
            self.log.write({"kind": "wait", "ticks": t0, "end_ticks": out["ticks"], "a": a,
                            "reason": out.get("reason"), "checksum": self.call("checksum"),
                            "snap": self.snapshot()})
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

    def note(self, method: str, **args: Any) -> None:
        """Log a non-world-changing meta decision (forecast, promise, abstain, flag, submit)."""
        if self.log:
            self.log.write({"kind": "meta", "ticks": self.state()["ticks"], "m": method, "a": args})

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
