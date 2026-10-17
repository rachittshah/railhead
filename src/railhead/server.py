"""Manage one headless OpenTTD dedicated-server process in an isolated instance directory.

Each instance gets its own ``openttd.cfg`` (``-c``), ``save/``, ``game/``, ``ai/`` and random
loopback ports, so many servers (e.g. world forks) can run side by side.
"""

from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from railhead.admin_client import AdminClient
from railhead.engine import find_openttd, scripts_dir

# Settings every Railhead server needs (evidence: docs/research/03-openttd-source.md §2, §4, §6).
BASE_SETTINGS: dict[str, dict[str, str]] = {
    "network": {
        "allow_insecure_admin_login": "true",  # loopback only
        "server_advertise": "false",
        "frame_freq": "0",
        "commands_per_frame": "1024",
        "commands_per_frame_server": "1024",
        "min_active_clients": "0",
        "pause_on_join": "false",
        "max_lag_time": "65535",
        "server_name": "railhead",
    },
    "construction": {"command_pause_level": "3"},  # build while paused (deterministic lockstep)
    "script": {"script_max_opcode_till_suspend": "250000", "script_max_memory_megabytes": "1024"},
    "ai": {"ai_in_multiplayer": "true"},
    "gui": {"threaded_saves": "false", "autosave_interval": "0"},
    "difficulty": {"max_no_competitors": "0"},
}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class ServerConfig:
    seed: int = 1
    map_x_log2: int = 6
    map_y_log2: int = 6
    landscape: str = "temperate"
    starting_year: int = 1950
    game_script: str | None = "RailheadBridge"
    game_script_settings: dict[str, int] = field(default_factory=dict)
    savegame: Path | None = None
    settings: dict[str, dict[str, str]] = field(default_factory=dict)
    debug: str | None = "script=2"  # OpenTTD -d levels; script>=2 logs script errors

    def merged_settings(self) -> dict[str, dict[str, str]]:
        out = {k: dict(v) for k, v in BASE_SETTINGS.items()}
        out.setdefault("game_creation", {}).update(
            map_x=str(self.map_x_log2),
            map_y=str(self.map_y_log2),
            landscape=self.landscape,
            starting_year=str(self.starting_year),
        )
        for section, kv in self.settings.items():
            out.setdefault(section, {}).update({k: str(v) for k, v in kv.items()})
        return out


class OpenTTDServer:
    def __init__(
        self, workdir: Path, config: ServerConfig, binary: Path | None = None, ready_timeout: float = 60.0
    ) -> None:
        # absolute: the engine runs with cwd=workdir, so a relative -c path would point nowhere
        self.workdir = Path(workdir).resolve()
        self.config = config
        self.binary = Path(binary) if binary else find_openttd()
        self.ready_timeout = ready_timeout
        self.admin_password = secrets.token_hex(12)
        self.admin_port = 0
        self.game_port = 0
        self.proc: subprocess.Popen | None = None
        self.log_path = self.workdir / "server.log"

    # ------------------------------------------------------------------ files
    @property
    def save_dir(self) -> Path:
        return self.workdir / "save"

    def _write_files(self) -> None:
        self.workdir.mkdir(parents=True, exist_ok=True)
        for sub in ("save", "game", "ai", "baseset", "scenario", "newgrf"):
            (self.workdir / sub).mkdir(exist_ok=True)
        # Link Railhead's Squirrel scripts (bridge GS + idle AI) into this instance's search path.
        try:
            sdir: Path | None = scripts_dir()
        except FileNotFoundError:
            sdir = None
        for kind in ("game", "ai"):
            src_root = sdir / kind if sdir else None
            if src_root and src_root.is_dir():
                for item in src_root.iterdir():
                    dst = self.workdir / kind / item.name
                    if not dst.exists():
                        os.symlink(item.resolve(), dst)
        settings = self.config.merged_settings()
        settings["network"]["server_admin_port"] = str(self.admin_port)
        settings["network"]["server_port"] = str(self.game_port)
        settings["network"]["admin_password"] = self.admin_password
        lines: list[str] = []
        for section, kv in settings.items():
            lines.append(f"[{section}]")
            lines += [f"{k} = {v}" for k, v in kv.items()]
            lines.append("")
        lines.append("[game_scripts]")
        if self.config.game_script:
            params = ",".join(f"{k}={v}" for k, v in self.config.game_script_settings.items())
            lines.append(f"{self.config.game_script} = {params}")
        lines.append("")
        lines += ["[ai_players]", ""]
        (self.workdir / "openttd.cfg").write_text("\n".join(lines))

    # ------------------------------------------------------------------ process
    def command(self) -> list[str]:
        cmd = [
            str(self.binary), "-D", f"127.0.0.1:{self.game_port}", "-c", str(self.workdir / "openttd.cfg"),
            "-x", "-X", "-G", str(self.config.seed),
        ]
        if self.config.savegame:
            cmd += ["-g", str(Path(self.config.savegame).resolve())]
        if self.config.debug:
            cmd += ["-d", self.config.debug]
        return cmd

    START_ATTEMPTS = 3

    def start(self) -> OpenTTDServer:
        """Launch the engine. free_port() can race with other engines started at the same moment (parallel
        replays), so a start that never answers, or whose admin join is refused, is retried on fresh ports."""
        last: Exception | None = None
        for _ in range(self.START_ATTEMPTS):
            self.admin_port, self.game_port = free_port(), free_port()
            self._write_files()
            self._launch()
            try:
                self._wait_ready()
                self._confirm_admin()
                return self
            except (TimeoutError, ConnectionError, OSError) as e:
                last = e
                self.stop()
        raise RuntimeError(f"openttd did not start after {self.START_ATTEMPTS} attempts: {last}")

    def _launch(self) -> None:
        with open(self.log_path, "ab") as log:  # the child keeps its own copy of the descriptor
            self.proc = subprocess.Popen(
                self.command(), cwd=self.workdir, stdin=subprocess.PIPE, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
            )

    def _confirm_admin(self) -> None:
        """A second join, after a short settle: proves the port is still our engine and it is still running."""
        time.sleep(0.3)
        if self.proc is not None and self.proc.poll() is not None:
            raise ConnectionError(f"openttd exited right after start; log:\n{self.tail_log()}")
        with AdminClient.connect("127.0.0.1", self.admin_port, self.admin_password, timeout=5.0):
            pass

    def _wait_ready(self) -> None:
        deadline = time.monotonic() + self.ready_timeout
        last_err: Exception | None = None
        while time.monotonic() < deadline:
            if self.proc and self.proc.poll() is not None:
                raise RuntimeError(f"openttd exited with {self.proc.returncode}; see {self.log_path}:\n{self.tail_log()}")
            try:
                with AdminClient.connect("127.0.0.1", self.admin_port, self.admin_password, timeout=5.0):
                    return
            except (OSError, TimeoutError, ConnectionError) as e:
                last_err = e
                time.sleep(0.1)
        raise TimeoutError(f"openttd not ready after {self.ready_timeout}s ({last_err}); log:\n{self.tail_log()}")

    def tail_log(self, n: int = 40) -> str:
        try:
            return "\n".join(self.log_path.read_text(errors="replace").splitlines()[-n:])
        except FileNotFoundError:
            return ""

    def stop(self, timeout: float = 5.0) -> None:
        if not self.proc or self.proc.poll() is not None:
            return
        try:
            if self.proc.stdin:
                self.proc.stdin.write(b"quit\n")
                self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            pass
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def admin(self, **kw) -> AdminClient:
        return AdminClient.connect("127.0.0.1", self.admin_port, self.admin_password, **kw)

    def __enter__(self) -> OpenTTDServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def remove(self) -> None:
        self.stop()
        shutil.rmtree(self.workdir, ignore_errors=True)
