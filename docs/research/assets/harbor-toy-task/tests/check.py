"""Verifier for the toy task. Writes /logs/verifier/reward.json (a flat
{str: number} map). Harbor reads reward.json in preference to reward.txt."""

import json
import os
import time
import urllib.request
from pathlib import Path


def fetch(url: str, method: str = "GET") -> bytes | None:
    try:
        req = urllib.request.Request(url, method=method)
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.read()
    except Exception:
        return None


def pid_alive(pid_file: str) -> int:
    try:
        pid = int(Path(pid_file).read_text().strip())
        os.kill(pid, 0)
        return 1
    except Exception:
        return 0


def read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except Exception:
        return ""


hello = fetch("http://localhost:8080/hello.txt")
state_raw = fetch("http://game:9000/state")
state = json.loads(state_raw) if state_raw else {}

entrypoint_alive = int(fetch("http://localhost:8080/") is not None and pid_alive("/run/svc/pid") == 1)
entrypoint_same_pid = int(read("/run/svc/pid") != "" and read("/run/svc/pid") == read("/tmp/svc_pid_seen_by_agent"))
cmd_service_alive = int(fetch("http://localhost:9090/") is not None)
agent_spawned_alive = int(fetch("http://localhost:8081/") is not None and pid_alive("/tmp/agent_svc.pid") == 1)
sidecar_same_boot = int(bool(state) and state.get("boot_id") == read("/tmp/game_boot_id_seen_by_agent"))
ticks = int(state.get("ticks", -1))
hello_ok = int(hello == b"railhead")
started_at = read("/run/svc/started_at")

rewards = {
    "reward": float(hello_ok and ticks == 3),
    "hello_served": hello_ok,
    "game_ticks": ticks,
    "entrypoint_service_alive": entrypoint_alive,
    "entrypoint_service_same_pid": entrypoint_same_pid,
    "cmd_service_alive": cmd_service_alive,
    "agent_spawned_service_alive": agent_spawned_alive,
    "sidecar_same_boot": sidecar_same_boot,
    "service_uptime_sec": int(time.time()) - int(started_at) if started_at else -1,
}

print(json.dumps({"rewards": rewards, "pid1": read("/proc/1/cmdline").replace("\x00", " "), "game_state": state}, indent=2))
Path("/logs/verifier/reward.json").write_text(json.dumps(rewards))
