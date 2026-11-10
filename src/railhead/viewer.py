"""Watch a Railhead world live in the OpenTTD GUI (macOS demo tooling, optional).

    railhead view <task_id> [--days N] [--speed PCT] [--log actions.jsonl] [--bounds x,y,w,h]
    railhead view --attach <state_dir> [--bounds x,y,w,h]

Loads the task's sealed start state, opens the 15.3 GUI client (RailheadViewer.app) as a spectator,
puts its window on a display found by name in the live display list (the LG monitor when one is
connected, else the main display; ``--bounds`` gives the window bounds in System Events points),
checks where the window landed, re-issues a recorded action log (default: the oracle's), then lets
the world run. Every osascript call has a timeout.
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


OSASCRIPT_TIMEOUT = 10  # seconds; a hung System Events (e.g. a pending Accessibility prompt) must not hang the CLI


def _osascript(args: list[str], check: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["osascript", *args], capture_output=True, text=True, check=check,
                              timeout=OSASCRIPT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"osascript did not answer in {OSASCRIPT_TIMEOUT} s") from None


def screens() -> list[dict]:
    """The connected displays, main display first: name and Cocoa frame (points, origin at the bottom left
    of the main display, y up)."""
    return json.loads(_osascript(["-l", "JavaScript", "-e", _SCREENS_JS], check=True).stdout)


def pick_display(displays: list[dict]) -> dict:
    """The LG monitor when one is connected (the owner's setup), else the main display (listed first)."""
    if not displays:
        raise RuntimeError("no display found")
    return next((d for d in displays if "LG" in d["name"].upper()), displays[0])


def display_bounds(displays: list[dict], d: dict, half: str = "full") -> tuple[int, int, int, int]:
    """Bounds of display ``d`` (or its lower half) in System Events coordinates (origin at the top left of
    the main display, y down)."""
    top = displays[0]["h"] - (d["y"] + d["h"])
    x, w, h = int(d["x"]), int(d["w"]), int(d["h"])
    if half == "lower":
        return x, int(top + h // 2), w, h // 2
    return x, int(top), w, h


def plan_window(displays: list[dict], bounds: tuple[int, int, int, int] | None = None
                ) -> tuple[tuple[int, int, int, int], tuple[int, int, int, int]]:
    """(target window bounds, area the window must land in). Without ``bounds``: the lower half of the
    picked display. With ``bounds``: those bounds, which must lie inside one connected display."""
    if bounds is None:
        d = pick_display(displays)
        return display_bounds(displays, d, "lower"), display_bounds(displays, d)
    for d in displays:
        area = display_bounds(displays, d)
        if inside(bounds, area):
            return bounds, area
    raise ValueError(f"--bounds {bounds} is not inside one display")


def parse_bounds(text: str) -> tuple[int, int, int, int]:
    """``x,y,w,h`` in System Events points (origin at the top left of the main display)."""
    try:
        x, y, w, h = (int(v) for v in text.split(","))
    except ValueError:
        raise ValueError(f"--bounds must be x,y,w,h (four integers), got {text!r}") from None
    if w <= 0 or h <= 0:
        raise ValueError(f"--bounds must be x,y,w,h with w and h above 0, got {text!r}")
    return x, y, w, h


def inside(window: tuple[int, int, int, int], area: tuple[int, int, int, int]) -> bool:
    wx, wy, ww, wh = window
    ax, ay, aw, ah = area
    return ax <= wx and ay <= wy and wx + ww <= ax + aw and wy + wh <= ay + ah


def _window_bounds() -> tuple[int, int, int, int] | None:
    script = ('tell application "System Events" to tell (first process whose bundle identifier is '
              '"org.railhead.viewer") to get {position, size} of window 1')
    try:
        r = _osascript(["-e", script])
    except RuntimeError:
        return None
    if r.returncode != 0:
        return None
    x, y, w, h = (int(float(v)) for v in r.stdout.replace(" ", "").strip().split(","))
    return x, y, w, h


def _open_viewer(args: list[str], target: tuple[int, int, int, int], area: tuple[int, int, int, int]) -> None:
    """Open the viewer; place it at ``target``; confirm it landed inside ``area``."""
    subprocess.run(["open", "-n", "-a", str(VIEWER_APP), "--args", *args], check=True)
    time.sleep(4)
    if not _place_window(target):
        raise RuntimeError("could not place the viewer window (grant Accessibility to the terminal)")
    got = _window_bounds()
    if got is None or not inside(got, area):
        subprocess.run(["pkill", "-f", "RailheadViewer.app"], capture_output=True, check=False)
        raise RuntimeError(f"viewer window at {got}, outside the display area {area}; closed it")
    print(f"viewer window {got} is inside the display area {area}", flush=True)


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
        try:
            if _osascript(["-e", script]).returncode == 0:
                return True
        except RuntimeError:
            pass  # a hung call counts as one failed try
        time.sleep(0.5)
    return False


def view(task_id: str, days: int = 365, speed: int = 300, log: Path | None = None,
         bounds: tuple[int, int, int, int] | None = None) -> None:
    tdir = task_dir(task_id)
    work = REPO_DIR / ".cache" / "view" / task_id
    target, area = plan_window(screens(), bounds)  # fails here, before any engine starts
    w = World.load(work / "world", tdir / "start.sav", speed=100)
    try:
        cfg = _viewer_config(work / "client", *target[2:])
        # 255 = join as spectator, never found a company
        _open_viewer(["-c", str(cfg), "-x", "-n", f"127.0.0.1:{w.server.game_port}#255", "-m", "null", "-s", "null"],
                     target, area)
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

    target, area = plan_window(screens(), bounds)
    info = Client(state_dir).request("info")
    cfg = _viewer_config(REPO_DIR / ".cache" / "view" / f"attach-{Path(state_dir).parent.name}", *target[2:])
    _open_viewer(["-c", str(cfg), "-x", "-n", f"127.0.0.1:{info['game_port']}#255", "-m", "null", "-s", "null"],
                 target, area)


def main(argv: list[str]) -> int:
    """``railhead view`` arguments (after the command name)."""
    import sys

    def opt(name: str) -> str | None:
        return argv[argv.index(name) + 1] if name in argv else None

    try:
        bounds = parse_bounds(b) if (b := opt("--bounds")) else None
        if argv and argv[0] == "--attach":
            attach(Path(argv[1]), bounds=bounds)
            return 0
        log = opt("--log")
        view(argv[0], days=int(opt("--days") or 365), speed=int(opt("--speed") or 300),
             log=Path(log) if log else None, bounds=bounds)
    except (RuntimeError, ValueError, IndexError) as e:  # bad arguments, no display, or a misplaced window
        print(f"railhead view: {e}", file=sys.stderr)
        return 1
    return 0
