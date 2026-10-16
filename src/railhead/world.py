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

from railhead.bridge import Bridge
from railhead.server import OpenTTDServer, ServerConfig

AGENT_AI = "RailheadIdle"


class World:
    def __init__(self, server: OpenTTDServer, speed: int = 0) -> None:
        self.server = server
        self.admin = server.admin()
        self.bridge = Bridge(self.admin)
        self.bridge.wait_ready(timeout=60)
        self.set_speed(speed)

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

    def step(self, *, days: int | None = None, ticks: int | None = None, timeout: float = 600.0) -> dict:
        if (days is None) == (ticks is None):
            raise ValueError("step needs exactly one of days= or ticks=")
        return self.call("step", timeout=timeout, **({"days": days} if days is not None else {"ticks": ticks}))

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
