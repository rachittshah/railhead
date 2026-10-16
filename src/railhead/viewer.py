"""Watch a Railhead world live in the OpenTTD GUI (macOS demo tooling, optional).

    railhead view <task_id> [--days N] [--speed PCT] [--log actions.jsonl] [--bounds x,y,w,h]

Loads the task's sealed start state, opens the 15.3 GUI client (RailheadViewer.app) as a spectator,
puts its window on the LG monitor (found by name in the live display list; the viewer refuses to
open if no LG display is connected), checks where the window landed, re-issues a recorded action log
(default: the oracle's), then lets the world run.
The viewer only watches: every world change still goes through the logged player interface.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from railhead.actionlog import read_log
from railhead.bridge import BridgeError
from railhead.engine import REPO_DIR
from railhead.task import task_dir
from railhead.world import World

VIEWER_APP = REPO_DIR / ".cache" / "RailheadViewer.app"

_SCREENS_JS = (
    'ObjC.import("AppKit"); var s = $.NSScreen.screens, o = [];'
    "for (var i = 0; i < s.count; i++) { var c = s.objectAtIndex(i), f = c.frame;"
    " o.push({name: ObjC.unwrap(c.localizedName), x: f.origin.x, y: f.origin.y, w: f.size.width, h: f.size.height}); }"
    "JSON.stringify(o)"
)


def screens() -> list[dict]:
    """The connected displays, main display first: name and Cocoa frame (points, origin at the bottom left
    of the main display, y up)."""
    r = subprocess.run(["osascript", "-l", "JavaScript", "-e", _SCREENS_JS], capture_output=True, text=True, check=True)
    return json.loads(r.stdout)


def lg_bounds(displays: list[dict], half: str = "lower") -> tuple[int, int, int, int]:
    """Window bounds on the LG monitor in System Events coordinates (origin at the top left of the main
    display, y down). Raises if no LG display is connected: the viewer never opens on another screen."""
    lg = next((d for d in displays if "LG" in d["name"].upper()), None)
    if lg is None:
        names = ", ".join(d["name"] for d in displays) or "none"
        raise RuntimeError(f"LG monitor not connected (displays: {names}); the viewer opens only on the LG monitor")
    top = displays[0]["h"] - (lg["y"] + lg["h"])
    x, w, h = int(lg["x"]), int(lg["w"]), int(lg["h"])
    if half == "lower":
        return x, int(top + h // 2), w, h // 2
    return x, int(top), w, h


def inside(window: tuple[int, int, int, int], area: tuple[int, int, int, int]) -> bool:
    wx, wy, ww, wh = window
    ax, ay, aw, ah = area
    return ax <= wx and ay <= wy and wx + ww <= ax + aw and wy + wh <= ay + ah


def _window_bounds() -> tuple[int, int, int, int] | None:
    script = ('tell application "System Events" to tell (first process whose bundle identifier is '
              '"org.railhead.viewer") to get {position, size} of window 1')
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    x, y, w, h = (int(float(v)) for v in r.stdout.replace(" ", "").strip().split(","))
    return x, y, w, h


def _open_on_lg(args: list[str], bounds: tuple[int, int, int, int] | None) -> None:
    """Refuse without an LG monitor; open the viewer; place it; confirm it is on the LG monitor."""
    displays = screens()
    area = lg_bounds(displays, half="full")  # raises when the LG monitor is missing
    target = bounds or lg_bounds(displays)
    subprocess.run(["open", "-n", "-a", str(VIEWER_APP), "--args", *args], check=True)
    time.sleep(4)
    if not _place_window(target):
        raise RuntimeError("could not place the viewer window (grant Accessibility to the terminal)")
    got = _window_bounds()
    if got is None or not inside(got, area):
        subprocess.run(["pkill", "-f", "RailheadViewer.app"], capture_output=True)
        raise RuntimeError(f"viewer window at {got}, outside the LG monitor {area}; closed it")
    print(f"viewer window {got} is on the LG monitor {area}", flush=True)


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
         bounds: tuple[int, int, int, int] | None = None) -> None:
    tdir = task_dir(task_id)
    work = REPO_DIR / ".cache" / "view" / task_id
    size = (bounds or lg_bounds(screens()))[2:]  # fails here, before any engine starts, without an LG monitor
    w = World.load(work / "world", tdir / "start.sav", speed=100)
    try:
        cfg = _viewer_config(work / "client", *size)
        # 255 = join as spectator, never found a company
        _open_on_lg(["-c", str(cfg), "-x", "-n", f"127.0.0.1:{w.server.game_port}#255", "-m", "null", "-s", "null"],
                    bounds)
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


def attach(state_dir: Path, bounds: tuple[int, int, int, int] | None = None) -> None:
    """Spectate the world a running daemon owns (e.g. a model playing live). Watch-only."""
    from railhead.client import Client

    size = (bounds or lg_bounds(screens()))[2:]
    info = Client(state_dir).request("info")
    cfg = _viewer_config(REPO_DIR / ".cache" / "view" / f"attach-{Path(state_dir).parent.name}", *size)
    _open_on_lg(["-c", str(cfg), "-x", "-n", f"127.0.0.1:{info['game_port']}#255", "-m", "null", "-s", "null"], bounds)
