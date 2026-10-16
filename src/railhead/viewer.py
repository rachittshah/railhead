"""Watch a Railhead world live in the OpenTTD GUI (macOS demo tooling, optional).

    railhead view <task_id> [--days N] [--speed PCT] [--log actions.jsonl] [--bounds x,y,w,h]

Loads the task's sealed start state, opens the 15.3 GUI client (RailheadViewer.app) as a spectator,
puts its window on the requested screen area (default: the portrait LG monitor left of the main
display), re-issues a recorded action log (default: the oracle's), then lets the world run.
The viewer only watches: every world change still goes through the logged player interface.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

from railhead.actionlog import read_log
from railhead.bridge import BridgeError
from railhead.engine import REPO_DIR
from railhead.task import task_dir
from railhead.world import World

VIEWER_APP = REPO_DIR / ".cache" / "RailheadViewer.app"
LG_BOUNDS = (-1080, 960, 1080, 960)  # x, y (top-left origin), w, h: lower half of the portrait LG


def _viewer_config(cfg_dir: Path, w: int, h: int) -> Path:
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = cfg_dir / "openttd.cfg"
    cfg.write_text(
        "[misc]\nfullscreen = false\n"
        f"resolution = {w},{h}\n"
        "[network]\nclient_name = railhead-viewer\n"
        "[gui]\nautosave_interval = 0\nzoom_min = 0\n"
    )
    for sub in ("save", "baseset"):
        (cfg_dir / sub).mkdir(exist_ok=True)
    return cfg


def _place_window(bounds: tuple[int, int, int, int], tries: int = 40) -> bool:
    x, y, w, h = bounds
    script = (
        'tell application "System Events" to tell (first process whose bundle identifier is '
        '"org.railhead.viewer")\n'
        "  set frontmost to true\n"
        f"  set position of window 1 to {{{x}, {y}}}\n"
        f"  set size of window 1 to {{{w}, {h}}}\n"
        "end tell"
    )
    for _ in range(tries):
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if r.returncode == 0:
            return True
        time.sleep(0.5)
    return False


def view(task_id: str, days: int = 365, speed: int = 300, log: Path | None = None,
         bounds: tuple[int, int, int, int] = LG_BOUNDS) -> None:
    tdir = task_dir(task_id)
    work = REPO_DIR / ".cache" / "view" / task_id
    w = World.load(work / "world", tdir / "start.sav", speed=100)
    try:
        cfg = _viewer_config(work / "client", bounds[2], bounds[3])
        subprocess.run(["open", "-n", "-a", str(VIEWER_APP), "--args", "-c", str(cfg), "-x",
                        "-n", f"127.0.0.1:{w.server.game_port}#255",  # 255 = join as spectator, never found a company
                        "-m", "null", "-s", "null"], check=True)
        time.sleep(4)
        print("window placed" if _place_window(bounds) else "could not place the window (accessibility)")
        log = log or (tdir / "oracle.jsonl")
        w.set_speed(speed)
        for rec in read_log(log):  # the recorded game, tick for tick (acts at their tick, waits by ticks)
            if rec.kind == "act":
                try:
                    w.call(rec.raw["m"], **(rec.raw.get("a") or {}))
                except BridgeError:
                    pass
                time.sleep(0.05)  # let the spectator see each piece go down
            elif rec.kind == "wait":
                st = w.step(ticks=int(rec.raw["end_ticks"]) - int(rec.raw["ticks"]))
                c = w.call("company")
                print(f"{st['ymd']}  money £{c.get('money'):,}  loan £{c.get('loan'):,}", flush=True)
        done = 0
        while done < days:  # 30-day chunks: the world runs continuously inside each step
            st = w.step(days=min(30, days - done))
            done += st.get("days", 30) or 30
            c = w.call("company")
            print(f"{st['ymd']}  money £{c.get('money'):,}  loan £{c.get('loan'):,}", flush=True)
    finally:
        w.close()


def attach(state_dir: Path, bounds: tuple[int, int, int, int] = LG_BOUNDS) -> None:
    """Spectate the world a running daemon owns (e.g. a model playing live). Watch-only."""
    from railhead.client import Client

    info = Client(state_dir).request("info")
    cfg = _viewer_config(REPO_DIR / ".cache" / "view" / f"attach-{Path(state_dir).parent.name}", bounds[2], bounds[3])
    subprocess.run(["open", "-n", "-a", str(VIEWER_APP), "--args", "-c", str(cfg), "-x",
                    "-n", f"127.0.0.1:{info['game_port']}#255", "-m", "null", "-s", "null"], check=True)
    time.sleep(4)
    print("window placed" if _place_window(bounds) else "could not place the window (accessibility)")
