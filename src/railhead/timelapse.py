"""Arena timelapse: every company's network growing side by side, with the money, from real replays.

    railhead timelapse <arena_dir> --task tasks/<id> --out <dir> [--expected docs/data/<arena>.json]
                       [--players a,b] [--jobs 4] [--sample-only | --render-only] [--resample]
                       [--control] [--theme dark,light] [--seconds 60] [--title "Arena 2"]

Two stages, the second re-runnable on its own:

1. **Sample** (needs the engine). Each player's action log is copied into ``<out>/<player>/`` and replayed
   from the task's sealed start savegame with ``railhead.replay.replay`` itself. To sample more often than
   the player waited (some waits are a whole year), every wait record is split at a fixed grid of
   absolute ticks (``GRID_DAYS`` apart) into contiguous sub-waits; only the last piece keeps the live
   checksum, which replay compares as usual. A wait that follows the player's actions also gets a
   1-tick piece first, so a route appears within one tick of being built. After the log, the world runs
   on to the horizon and through the run-off exactly as ``railhead.verify.score`` does, in steps that
   land on the same targets. At every piece end the world is paused and only read: date, cash, loan,
   net worth, stations, routes (consecutive stops of each vehicle's orders, by mode) and vehicles by
   mode. The replayed net worth at the horizon and after the run-off is compared with the verified
   result; ``--control`` also replays the original log unsplit and unsampled, scores it the way the
   verifier does, and compares the final checksum. Samples land in ``<out>/samples/<player>.json``.

2. **Render** (matplotlib + ffmpeg, no engine). Frames are drawn from the samples and piped to ffmpeg:
   ``timelapse-<theme>.mp4`` (main cut, 1920x1080, 30 fps), ``timelapse-15s-<theme>.mp4`` (every 4th frame
   of it) and ``timelapse-loop.webm`` / ``.gif`` (small, for a README), plus PNG stills. On screen a route
   and a station appear at the first sample where they exist and stay until a sample no longer has
   them (a step function, never ahead of the replay); money is interpolated linearly between samples.
"""

from __future__ import annotations

import bisect
import hashlib
import itertools
import json
import math
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

from railhead.figures import THEMES, money, vendor
from railhead.render import MODES, pixel, routes

TICKS_PER_DAY = 74
GRID_DAYS = 28  # <= the shortest month: every calendar month holds at least one sample
GRID_TICKS = GRID_DAYS * TICKS_PER_DAY
SAMPLE_VERSION = 2


# ============================================================================================ sampling
def split_log(records: list[dict], grid: int = GRID_TICKS) -> list[dict]:
    """The same log with every wait cut into contiguous sub-waits at absolute multiples of ``grid`` ticks
    (plus one tick after an act batch). Acts, meta records and the header are untouched; the last piece
    of a wait keeps the original record (seq, end_ticks, checksum); earlier pieces carry ``split_of``."""
    out: list[dict] = []
    after_acts = False
    for r in records:
        kind = r.get("kind")
        if kind != "wait":
            out.append(r)
            if kind == "act":
                after_acts = True
            continue
        t0, t1 = int(r["ticks"]), int(r["end_ticks"])
        cuts = set(range((t0 // grid + 1) * grid, t1, grid))
        if after_acts and t0 + 1 < t1:
            cuts.add(t0 + 1)
        edges = [t0, *sorted(cuts), t1]
        for i, (a, b) in enumerate(itertools.pairwise(edges)):
            last = i == len(edges) - 2
            piece = dict(r, ticks=a) if last else {"kind": "wait", "ticks": a, "end_ticks": b,
                                                   "a": {"ticks": b - a}, "split_of": r.get("seq")}
            out.append(piece)
        after_acts = False
    return out


def day_chunks(today: int, target: int, grid_days: int = GRID_DAYS) -> list[int]:
    """Day-steps from ``today`` to ``target``, at most ``grid_days`` each, landing exactly on target."""
    out, d = [], today
    while d < target:
        n = min(grid_days, target - d)
        out.append(n)
        d += n
    return out


def legs_of(orders: dict[int, tuple[str, list[dict]]]) -> list[list]:
    """[[mode, a, b, vehicles], ...] sorted, from vehicle orders (railhead.render.routes)."""
    return sorted([m, a, b, n] for (m, a, b), n in routes(orders).items())


def make_sample(*, ticks: int, ymd: str, phase: str, money_: int, loan: int, stations: list[dict],
                orders: dict[int, tuple[str, list[dict]]], vehicles: list[dict]) -> dict:
    """One sample record (the JSON shape the renderer reads)."""
    fleet = Counter(v["vtype"] for v in vehicles)
    return {"t": int(ticks), "date": ymd, "phase": phase, "cash": int(money_), "loan": int(loan),
            "net_worth": int(money_) - int(loan),
            "stations": [[s["id"], s["x"], s["y"], sorted(s.get("types") or [])] for s in stations],
            "routes": legs_of(orders), "vehicles": {m: fleet.get(m, 0) for m in MODES}}


class Sampler:
    """``on_wait`` hook: reads the paused world (queries only, never an action), every vehicle's
    orders included, at every sample."""

    def __init__(self) -> None:
        self.samples: list[dict] = []
        self.calls = 0
        self.seconds = 0.0
        self.phase = "play"

    def __call__(self, world, rec: dict) -> None:
        self.take(world)

    def take(self, world) -> dict:
        t0 = time.monotonic()
        st = world.state()
        cash, loan = finances(world)
        stations = world.call("stations")
        vehicles = world.call("vehicles")
        orders = {v["id"]: (v["vtype"], world.call("orders", vehicle=v["id"])) for v in vehicles}
        self.calls += 5 + len(vehicles)
        s = make_sample(ticks=st["ticks"], ymd=st["ymd"], phase=self.phase, money_=cash, loan=loan,
                        stations=stations, orders=orders, vehicles=vehicles)
        self.samples.append(s)
        self.seconds += time.monotonic() - t0
        return s


def finances(world) -> tuple[int, int]:
    """(money, loan) of the bound company, read like railhead.verify._net_worth."""
    me = world.state()["company"]
    for c in world.call("companies"):
        if c["id"] == me:
            return int(c["money"]), int(c["loan"])
    return 0, 0


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def probe(task_dir: Path, out: Path) -> dict:
    """Start point and base map, from one engine that loads the start savegame and does nothing else:
    start date/ticks (what the verifier uses), the start sample (the same for every player) and the
    engine's minimap (land, water, towns). Cached in ``<out>/base/``."""
    from railhead.world import World

    base = Path(out) / "base"
    meta = base / "probe.json"
    sav = Path(task_dir) / "start.sav"
    if meta.exists() and json.loads(meta.read_text()).get("start_sav") == _sha(sav):
        return json.loads(meta.read_text())
    base.mkdir(parents=True, exist_ok=True)
    work = base / "work"
    shutil.rmtree(work, ignore_errors=True)
    with World.load(work, sav) as w:
        st = w.state()
        cash, loan = finances(w)
        info = w.call("map_info")
        start = make_sample(ticks=st["ticks"], ymd=st["ymd"], phase="play", money_=cash, loan=loan,
                            stations=w.call("stations"), orders={}, vehicles=w.call("vehicles"))
        name = "railhead_timelapse_base"
        shot = w.server.workdir / "screenshot" / f"{name}.png"
        shot.unlink(missing_ok=True)
        w.admin.rcon(f"screenshot minimap {name}")
        deadline = time.monotonic() + 30
        while not shot.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        if not shot.exists():
            raise RuntimeError(f"the engine did not write {shot}")
        time.sleep(0.5)  # let the PNG writer finish
        shutil.copy(shot, base / "minimap.png")
    shutil.rmtree(work, ignore_errors=True)
    doc = {"start_sav": _sha(sav), "start_date": int(st["date"]), "start_ticks": int(st["ticks"]),
           "start_ymd": st["ymd"], "size_x": info["size_x"], "size_y": info["size_y"], "start": start}
    meta.write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def expected_results(arena_dir: Path, player: str, expected_doc: dict | None) -> dict:
    """Verified net worth (horizon, after run-off): the player's verify/reward.json and, if given, the
    exported arena document. Both are reported; they must agree with each other and with the replay."""
    out: dict = {}
    rj = Path(arena_dir) / player / "verify" / "reward.json"
    if rj.exists():
        v = json.loads(rj.read_text())
        out["reward_json"] = {"net_worth": v.get("net_worth"), "net_worth_horizon": v.get("net_worth_horizon")}
    if expected_doc:
        for r in expected_doc.get("results", []):
            if r.get("player") == player:
                out["arena_doc"] = {"net_worth": r.get("net_worth"), "net_worth_horizon": r.get("net_worth_horizon")}
    return out


def sample_player(arena_dir: Path, player: str, task_dir: Path, out: Path, start: dict,
                  expected_doc: dict | None = None, keep_work: bool = False) -> dict:
    """Replay one player's log with sampling; write ``<out>/samples/<player>.json`` and return it."""
    from railhead.replay import replay
    from railhead.task import TaskSpec

    out = Path(out)
    pdir = out / player
    pdir.mkdir(parents=True, exist_ok=True)
    log = pdir / "actions.jsonl"
    shutil.copy(Path(arena_dir) / player / "state" / "run" / "actions.jsonl", log)
    records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    split = pdir / "actions.split.jsonl"
    split.write_text("".join(json.dumps(r, separators=(",", ":"), sort_keys=True) + "\n" for r in split_log(records)))
    spec = TaskSpec.load(task_dir)
    work = pdir / "work"
    shutil.rmtree(work, ignore_errors=True)
    sampler = Sampler()
    t_start = time.monotonic()
    res = replay(Path(task_dir) / "start.sav", split, work, keep_open=True, on_wait=sampler)
    world = res.world
    try:
        sampler.phase = "idle"  # the log has ended; the company runs on its own until the horizon
        # the same targets as railhead.verify.score: start_date + horizon_days, then runoff_days more
        end_date = int(start["start_date"]) + spec.horizon_days
        for n in day_chunks(world.state()["date"], end_date):
            world.step(days=n)
            sampler.take(world)
        horizon_ticks = world.state()["ticks"]
        cash, loan = finances(world)
        nw_horizon = cash - loan
        sampler.phase = "runoff"
        today = world.state()["date"]
        for n in day_chunks(today, today + spec.runoff_days):
            world.step(days=n)
            sampler.take(world)
        final_checksum = world.call("checksum")
        cash, loan = finances(world)
        nw_final = cash - loan
    finally:
        world.close()
        if not keep_work:
            shutil.rmtree(work, ignore_errors=True)
    exp = expected_results(arena_dir, player, expected_doc)
    mismatches = [f"{src}.{k}: expected {v[k]}, replay {got}" for src, v in exp.items()
                  for k, got in (("net_worth", nw_final), ("net_worth_horizon", nw_horizon)) if v.get(k) != got]
    doc = {
        "version": SAMPLE_VERSION, "player": player, "task": Path(task_dir).name, "log_sha256": _sha(log),
        "last_act_ticks": max((int(r["ticks"]) for r in records if r.get("kind") == "act"), default=None),
        "grid_days": GRID_DAYS, "start_sav": start["start_sav"], "horizon_ticks": horizon_ticks,
        "check": {"valid": res.valid, "divergences": res.divergences, "acts": res.acts,
                  "failed_acts": res.failed_acts, "waits": res.waits,
                  "net_worth_horizon": nw_horizon, "net_worth": nw_final, "final_checksum": final_checksum,
                  "expected": exp, "mismatches": mismatches, "ok": res.valid and not mismatches},
        "cost": {"seconds": round(time.monotonic() - t_start, 1), "query_seconds": round(sampler.seconds, 1),
                 "queries": sampler.calls},
        "samples": [start["start"], *sampler.samples],
    }
    sdir = out / "samples"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / f"{player}.json").write_text(json.dumps(doc, separators=(",", ":")) + "\n")
    return doc


def control_player(arena_dir: Path, player: str, task_dir: Path, out: Path, start: dict) -> dict:
    """The verifier's path with no sampling: unsplit replay, one step to the horizon, one through the
    run-off, then the checksum. Written to ``<out>/samples/<player>.control.json``."""
    from railhead.replay import replay
    from railhead.task import TaskSpec

    out = Path(out)
    log = out / player / "actions.jsonl"
    if not log.exists():
        log.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(arena_dir) / player / "state" / "run" / "actions.jsonl", log)
    spec = TaskSpec.load(task_dir)
    work = out / player / "work-control"
    shutil.rmtree(work, ignore_errors=True)
    t0 = time.monotonic()
    res = replay(Path(task_dir) / "start.sav", log, work, keep_open=True)
    world = res.world
    try:
        end_date = int(start["start_date"]) + spec.horizon_days
        today = world.state()["date"]
        if today < end_date:
            world.step(days=end_date - today)
        m, l = finances(world)
        nw_h = m - l
        world.step(days=spec.runoff_days)
        m, l = finances(world)
        doc = {"player": player, "valid": res.valid, "divergences": len(res.divergences),
               "net_worth_horizon": nw_h, "net_worth": m - l, "final_checksum": world.call("checksum"),
               "seconds": round(time.monotonic() - t0, 1)}
    finally:
        world.close()
        shutil.rmtree(work, ignore_errors=True)
    (out / "samples" / f"{player}.control.json").write_text(json.dumps(doc, indent=1) + "\n")
    return doc


def _sample_job(args: tuple) -> dict:
    arena_dir, player, task_dir, out, start, expected_doc, control = args
    try:
        doc = sample_player(Path(arena_dir), player, Path(task_dir), Path(out), start, expected_doc)
        row = {"player": player, **{k: doc["check"][k] for k in ("ok", "net_worth", "net_worth_horizon", "mismatches")},
               "divergences": len(doc["check"]["divergences"]), "samples": len(doc["samples"]), **doc["cost"]}
        if control:
            c = control_player(Path(arena_dir), player, Path(task_dir), Path(out), start)
            row["control_checksum_equal"] = c["final_checksum"] == doc["check"]["final_checksum"]
            row["control_net_worth"] = c["net_worth"]
        return row
    except Exception as e:  # noqa: BLE001 - one player's failure is reported, not hidden, and the rest go on
        return {"player": player, "ok": False, "error": f"{type(e).__name__}: {e}"}


def players_of(arena_dir: Path) -> list[str]:
    return sorted(p.name for p in Path(arena_dir).iterdir() if (p / "state" / "run" / "actions.jsonl").exists())


def cached(out: Path, arena_dir: Path, player: str) -> bool:
    f = Path(out) / "samples" / f"{player}.json"
    if not f.exists():
        return False
    try:
        doc = json.loads(f.read_text())
    except ValueError:
        return False
    log = Path(arena_dir) / player / "state" / "run" / "actions.jsonl"
    return doc.get("version") == SAMPLE_VERSION and doc.get("log_sha256") == _sha(log)


def sample_all(arena_dir: Path, task_dir: Path, out: Path, players: list[str], jobs: int = 4,
               expected_doc: dict | None = None, resample: bool = False, control: bool = False) -> list[dict]:
    from concurrent.futures import ProcessPoolExecutor

    start = probe(task_dir, out)
    todo = [p for p in players if resample or not cached(out, arena_dir, p)]
    rows = [{"player": p, "cached": True} for p in players if p not in todo]
    with ProcessPoolExecutor(max_workers=max(1, min(4, jobs))) as ex:
        for row in ex.map(_sample_job, [(str(arena_dir), p, str(task_dir), str(out), start, expected_doc, control)
                                        for p in todo]):
            print(json.dumps(row), flush=True)
            rows.append(row)
    return rows


# ============================================================================================ timing
def display_date(t: float, t0: int, ymd0: str) -> date:
    """Game date at replay tick ``t`` (74 ticks a day from the start savegame's date)."""
    return date.fromisoformat(ymd0) + timedelta(days=math.floor((t - t0) / TICKS_PER_DAY))


def year_of(d: date) -> float:
    return d.year + (d.timetuple().tm_yday - 1) / (366 if d.year % 4 == 0 else 365)


def schedule(n_frames: int, t0: float, t1: float, hold_start: int, hold_end: int) -> list[float]:
    """Replay tick shown in each frame: a still start, uniform time, a still end."""
    motion = n_frames - hold_start - hold_end
    if motion < 2:
        raise ValueError("not enough frames for the holds")
    return ([float(t0)] * hold_start + [t0 + (t1 - t0) * i / (motion - 1) for i in range(motion)]
            + [float(t1)] * hold_end)


def frame_of(t: float, sched: list[float]) -> int:
    """First frame showing tick ``t`` or later (the last frame if ``t`` is past the end)."""
    return min(bisect.bisect_left(sched, t), len(sched) - 1)


def at_or_before(ts: list[int], t: float) -> int:
    """Index of the last sample taken at or before ``t`` (0 before the first): what the map shows."""
    return max(0, bisect.bisect_right(ts, t) - 1)


def money_at(ts: list[int], values: list[int], t: float) -> float:
    """Linear interpolation between the samples around ``t``; held flat outside them."""
    if t <= ts[0]:
        return float(values[0])
    if t >= ts[-1]:
        return float(values[-1])
    i = bisect.bisect_right(ts, t) - 1
    a, b = ts[i], ts[i + 1]
    return values[i] + (values[i + 1] - values[i]) * (t - a) / (b - a)


NUMBER_WORDS = {2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine", 10: "ten",
                11: "eleven", 12: "twelve"}


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def spread(ys: list[float], gap: float) -> list[float]:
    """Label positions for ascending ``ys``: each pushed up just enough to clear the one below."""
    out: list[float] = []
    for y in ys:
        out.append(max(y, out[-1] + gap) if out else y)
    return out


def route_width(n: int) -> float:
    """Line width in points for a route with ``n`` vehicles."""
    return min(5.0, 1.0 + 0.6 * math.sqrt(max(n, 1)))


# ============================================================================================ layout
W, H = 1920, 1080


@dataclass(frozen=True)
class Box:
    x: float
    y: float  # from the top, pixels
    w: float
    h: float

    def frac(self, width: int = W, height: int = H) -> tuple[float, float, float, float]:
        """matplotlib figure fraction (left, bottom, width, height)."""
        return self.x / width, 1 - (self.y + self.h) / height, self.w / width, self.h / height

    def overlaps(self, o: Box) -> bool:
        return self.x < o.x + o.w and o.x < self.x + self.w and self.y < o.y + o.h and o.y < self.y + self.h


def layout(n: int = 11, width: int = W, height: int = H, cols: int = 4) -> dict:
    """Pixel boxes: header, one cell per player (label band + square map), a legend cell, the money
    column (bars on top, lines below). 16 px gutters."""
    g, head, band = 16, 84, 46
    rows = math.ceil((n + 1) / cols)
    grid = Box(g, head + 4, round(width * 0.672), height - head - 4 - g)
    cw, ch = grid.w / cols, grid.h / rows
    side = min(cw - 14, ch - band - 10)
    cells, maps, labels = [], [], []
    for k in range(rows * cols):
        r, c = divmod(k, cols)
        cell = Box(grid.x + c * cw, grid.y + r * ch, cw, ch)
        cells.append(cell)
        labels.append(Box(cell.x + 4, cell.y + 2, side, band - 4))
        maps.append(Box(cell.x + 4, cell.y + band, side, side))
    rx = grid.x + grid.w + 28
    right = Box(rx, grid.y, width - g - rx, grid.h)
    bars = Box(right.x, right.y + 34, right.w, round(right.h * 0.47) - 34)
    lines_top = bars.y + bars.h + 92
    lines = Box(right.x + 52, lines_top, right.w - 84, right.y + right.h - lines_top - 40)
    return {"header": Box(g, 0, width - 2 * g, head), "cells": cells[:n], "labels": labels[:n], "maps": maps[:n],
            "legend": cells[n] if n < len(cells) else None, "right": right, "bars": bars, "lines": lines}


def quiet_base(img, t: dict):
    """The engine minimap reduced to three quiet tones (land, water, towns) so the routes carry the colour."""
    import numpy as np

    def rgb(h: str):
        return np.array([int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)])

    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    out = np.empty(img.shape[:2] + (3,))
    out[:] = rgb(t["base_land"])
    out[(b > r + 0.08) & (b > g + 0.02)] = rgb(t["base_water"])
    out[(r > g + 0.15) & (r > b + 0.15)] = rgb(t["base_town"])
    return out


BASE_TONES = {"dark": {"base_land": "#3b3b37", "base_water": "#141b23", "base_town": "#5e5d56"},
              "light": {"base_land": "#e3e2db", "base_water": "#c5d0dc", "base_town": "#b3b2a9"}}


# ============================================================================================ scene
@dataclass
class Track:
    """One player's samples, ready for per-frame lookups."""
    player: str
    ts: list[int]
    nw: list[int]
    samples: list[dict]
    play_end: int
    final: int
    last_act: int


def tracks(docs: list[dict]) -> list[Track]:
    out = []
    for d in docs:
        ss = d["samples"]
        play = [s["t"] for s in ss if s["phase"] == "play"]
        play_end = play[-1] if play else ss[0]["t"]
        last_act = d.get("last_act_ticks")
        out.append(Track(d["player"], [s["t"] for s in ss], [s["net_worth"] for s in ss], ss, play_end,
                         ss[-1]["net_worth"], play_end if last_act is None else last_act))
    out.sort(key=lambda k: -k.final)
    return out


class Scene:
    """The whole frame: static artists drawn once (blitted background), dynamic ones redrawn per frame."""

    def __init__(self, trs: list[Track], base_png: Path, start: dict, horizon_t: int, end_t: int, theme: str,
                 info: dict) -> None:
        import matplotlib.image as mpimg
        from matplotlib.collections import LineCollection
        from matplotlib.lines import Line2D

        from railhead.figures import _plt

        self.t = t = dict(THEMES[theme], **BASE_TONES[theme])
        plt = _plt(t)
        self.plt, self.trs, self.start = plt, trs, start
        self.t0, self.ymd0 = int(start["start_ticks"]), start["start_ymd"]
        self.horizon_t, self.end_t = horizon_t, end_t
        self.years = round(info["horizon_days"] / 365)
        self.size_x, self.size_y = int(start["size_x"]), int(start["size_y"])
        L = self.L = layout(len(trs))
        fig = self.fig = plt.figure(figsize=(W / 100, H / 100), dpi=100)
        base = quiet_base(mpimg.imread(base_png)[..., :3], t)
        dyn = self.dyn = []

        def text(x, y, s, **kw):  # pixel coordinates from the top-left
            a = fig.text(x / W, 1 - y / H, s, **kw)
            return a

        def live(a):
            a.set_animated(True)
            dyn.append(a)
            return a

        # header
        n_word = NUMBER_WORDS.get(len(trs), str(len(trs)))
        y_word = NUMBER_WORDS.get(self.years, str(self.years))
        text(16, 14, f"{info['title']}: {n_word} models, one map, {y_word} years", fontsize=22, fontweight="bold", va="top",
             color=t["ink"])
        text(16, 54, f"One transport company per model on the same {self.size_x}×{self.size_y} OpenTTD map "
             f"({info['task']}), each replayed "
             "from its own action log; ordered by final net worth.", fontsize=12.5, va="top", color=t["ink2"])
        self.year = live(text(W - 16, 8, "", fontsize=40, fontweight="bold", ha="right", va="top", color=t["ink"]))
        self.month = live(text(W - 150, 16, "", fontsize=17, ha="right", va="top", color=t["ink2"]))
        self.phase = live(text(W - 150, 44, "", fontsize=12.5, ha="right", va="top", color=t["ink"]))

        self.panels = []
        for k, tr in enumerate(trs):
            mb, lb = L["maps"][k], L["labels"][k]
            ax = fig.add_axes(mb.frac())
            ax.imshow(base, interpolation="bilinear", extent=(-0.5, self.size_x - 0.5, self.size_y - 0.5, -0.5))
            ax.set_xlim(-0.5, self.size_x - 0.5)
            ax.set_ylim(self.size_y - 0.5, -0.5)
            ax.axis("off")
            v = vendor(tr.player)
            fig.patches.append(plt.Rectangle(((lb.x) / W, 1 - (lb.y + 19) / H), 10 / W, 10 / H,
                                             transform=fig.transFigure, color=t[v], figure=fig))
            text(lb.x + 15, lb.y + 4, f"{k + 1}  {tr.player}", fontsize=12.5, fontweight="bold", va="top",
                 color=t["ink"])
            nw = live(text(lb.x + lb.w, lb.y + 1, "", fontsize=15, fontweight="bold", ha="right", va="top",
                           color=t["ink"]))
            stats = live(text(lb.x + 15, lb.y + 25, "", fontsize=10.5, va="top", color=t["ink2"]))
            lc = live(LineCollection([], capstyle="round", alpha=0.95))
            ax.add_collection(lc)
            lc.set_animated(True)
            st = live(ax.scatter([], [], s=9, color=t["ink"], edgecolors=t["surface"], linewidths=0.5, zorder=5))
            note = live(ax.text(0.5, 0.03, "", transform=ax.transAxes, ha="center", va="bottom", fontsize=10.5,
                                color=t["ink"], bbox={"boxstyle": "round,pad=0.3", "fc": t["surface"],
                                                      "ec": "none", "alpha": 0.85}))
            self.panels.append({"ax": ax, "nw": nw, "stats": stats, "lc": lc, "st": st, "note": note})

        # legend cell
        if L["legend"] is not None:
            lg = L["legend"]
            text(lg.x + 6, lg.y + 8, "Routes, by transport mode", fontsize=12.5, fontweight="bold", va="top",
                 color=t["ink"])
            for i, m in enumerate(("air", "rail", "road", "water")):
                y = lg.y + 44 + i * 27
                fig.lines.append(Line2D([(lg.x + 8) / W, (lg.x + 46) / W], [1 - y / H] * 2, color=t[m],
                                        linewidth=3.5, transform=fig.transFigure, figure=fig))
                text(lg.x + 56, y, m, fontsize=12, va="center", color=t["ink"])
            y = lg.y + 44 + 4 * 27
            fig.lines.append(Line2D([(lg.x + 27) / W], [1 - y / H], marker="o", markersize=7, color=t["ink"],
                                    markeredgecolor=t["surface"], transform=fig.transFigure, figure=fig))
            text(lg.x + 56, y, "station", fontsize=12, va="center", color=t["ink"])
            text(lg.x + 6, lg.y + 44 + 5 * 27 - 4,
                 "A line joins consecutive stops in a\nvehicle's orders; width = vehicles on it.\n"
                 f"Every map and number is read from the\nreplay every {GRID_DAYS} game days (and one tick\n"
                 "after each batch of actions). Money is\ninterpolated linearly between samples.",
                 fontsize=10, va="top", color=t["ink2"], linespacing=1.35)

        # money column: bars
        bx = L["bars"]
        text(bx.x, bx.y - 30, "Net worth (cash − loan)", fontsize=14, fontweight="bold", va="top", color=t["ink"])
        name_w = 196
        self.bar_ax = bax = fig.add_axes(Box(bx.x + name_w, bx.y, bx.w - name_w - 92, bx.h).frac())
        top = max(tr.final for tr in trs)
        low = min(0, min(min(tr.nw) for tr in trs))
        bax.set_xlim(min(low, -top * 0.004) / 1e6, top / 1e6)
        bax.set_ylim(len(trs) - 0.5, -0.5)
        bax.axis("off")
        bax.axvline(0, color=t["ink2"], linewidth=0.8)
        self.bars, self.bar_txt = [], []
        for i, tr in enumerate(trs):
            text(bx.x + name_w - 10, bx.y + (i + 0.5) * bx.h / len(trs), tr.player, fontsize=11.5, ha="right",
                 va="center", color=t["ink"])
            (rect,) = bax.barh([i], [0], height=0.64, color=t[vendor(tr.player)])
            self.bars.append(live(rect))
            self.bar_txt.append(live(bax.text(0, i, "", va="center", ha="left", fontsize=11.5, color=t["ink"],
                                              clip_on=False)))
        text(bx.x, bx.y + bx.h + 12, f"Doing nothing ends the run-off at {money(info['w_idle'])} (certified reference).",
             fontsize=10, va="top", color=t["ink2"])

        # money column: lines over time
        lx = L["lines"]
        self.line_ax = lax = fig.add_axes(lx.frac())
        y0, y1 = year_of(display_date(self.t0, self.t0, self.ymd0)), year_of(display_date(end_t, self.t0, self.ymd0))
        yh = year_of(display_date(horizon_t, self.t0, self.ymd0))
        lax.set_xlim(y0, y1)
        lax.set_ylim(min(-0.5, low / 1e6), top / 1e6 * 1.06)
        lax.axvspan(yh, y1, color=t["grid"], alpha=0.9, linewidth=0)
        lax.axvline(yh, color=t["ink2"], linewidth=0.9)
        xt = lax.get_xaxis_transform()
        lax.text((y0 + yh) / 2, 1.015, "mandate: the models decide", transform=xt, ha="center", va="bottom",
                 fontsize=10.5, color=t["ink2"])
        lax.text((yh + y1) / 2, 1.015, "run-off", transform=xt, ha="center", va="bottom", fontsize=10.5,
                 color=t["ink"])
        lax.yaxis.grid(True, color=t["grid"], linewidth=0.8)
        lax.set_axisbelow(True)
        lax.set_xticks(range(int(y0), int(y1) + 1, 2))
        lax.tick_params(length=0, labelsize=10.5)
        lax.set_ylabel("£ million", fontsize=10.5)
        text(lx.x - 52, lx.y - 56, "Net worth over time", fontsize=14, fontweight="bold", va="top", color=t["ink"])
        self.series, self.ends = [], []
        for k, tr in enumerate(trs):
            years = [year_of(display_date(x, self.t0, self.ymd0)) for x in tr.ts]
            (ln,) = lax.plot([], [], color=t[vendor(tr.player)], linewidth=1.6, alpha=0.9, solid_capstyle="round")
            self.series.append((live(ln), years))
            self.ends.append(live(lax.text(0, 0, str(k + 1), fontsize=10, fontweight="bold", va="center",
                                           ha="left", color=t[vendor(tr.player)], clip_on=False)))
        self.ylim = lax.get_ylim()
        self.cursor = live(lax.axvline(y0, color=t["ink"], linewidth=1.0))
        self.bg = None

    # ---------------------------------------------------------------------------------------- per frame
    def update(self, now: float) -> None:
        t = self.t
        d = display_date(now, self.t0, self.ymd0)
        self.year.set_text(str(d.year))
        self.month.set_text(d.strftime("%B"))
        if now > self.horizon_t:
            self.phase.set_text("Run-off: no more decisions")
        else:
            year_n = min(self.years, int((now - self.t0) / TICKS_PER_DAY // 365) + 1)
            self.phase.set_text(f"Mandate, year {year_n} of {self.years}")
        top = max(tr.final for tr in self.trs)
        for k, tr in enumerate(self.trs):
            p = self.panels[k]
            s = tr.samples[at_or_before(tr.ts, now)]
            nw = money_at(tr.ts, tr.nw, now)
            p["nw"].set_text(money(nw))
            nveh = sum(s["vehicles"].values())
            p["stats"].set_text(" · ".join(plural(n, w) for n, w in ((nveh, "vehicle"), (len(s["stations"]), "station"),
                                                                     (len(s["routes"]), "route"))))
            at = {sid: pixel(x, y, self.size_x) for sid, x, y, _ in s["stations"]}
            legs = sorted((r for r in s["routes"] if r[1] in at and r[2] in at), key=lambda r: r[3])
            p["lc"].set_segments([[at[a], at[b]] for _, a, b, _ in legs])
            p["lc"].set_color([t[m] for m, _, _, _ in legs])
            p["lc"].set_linewidth([route_width(n) for *_, n in legs])
            p["st"].set_offsets(list(at.values()) if at else [[float("nan"), float("nan")]])
            ended = tr.play_end < self.horizon_t and now > tr.last_act
            p["note"].set_text(f"last action {display_date(tr.last_act, self.t0, self.ymd0):%b %Y}; runs on alone"
                               if ended else "")
            p["note"].set_visible(ended)
            self.bars[k].set_width(nw / 1e6)
            self.bar_txt[k].set_position((max(nw, 0) / 1e6 + top / 1e6 * 0.015, k))
            self.bar_txt[k].set_text(money(nw))
        yr = year_of(d)
        ends = []
        for k, (ln, years) in enumerate(self.series):
            tr = self.trs[k]
            i = at_or_before(tr.ts, now)
            xs = years[: i + 1] + [yr]
            ys = [v / 1e6 for v in tr.nw[: i + 1]] + [money_at(tr.ts, tr.nw, now) / 1e6]
            ln.set_data(xs, ys)
            ends.append((ys[-1], k))
        lo, hi = self.ylim
        gap = (hi - lo) * 0.045  # ~one label height
        named = sorted((e for e in ends if e[0] > (hi - lo) * 0.06), reverse=True)  # near zero: the bars name them
        for label in self.ends:
            label.set_visible(False)
        # from the top down, so the leaders sit exactly at their line ends
        for (y, k), ly in zip(named, [-v for v in spread([-y for y, _ in named], gap)]):
            if y - ly <= 1.5 * gap:  # never park a number far from its line
                self.ends[k].set_visible(True)
                self.ends[k].set_position((yr + 0.06, ly))
        self.cursor.set_xdata([yr, yr])

    def render(self, now: float) -> memoryview:
        canvas = self.fig.canvas
        if self.bg is None:
            canvas.draw()
            self.bg = canvas.copy_from_bbox(self.fig.bbox)
        self.update(now)
        canvas.restore_region(self.bg)
        for a in self.dyn:
            if a.get_visible():
                self.fig.draw_artist(a)
        return canvas.buffer_rgba()

    def close(self) -> None:
        self.plt.close(self.fig)


# ============================================================================================ encode
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FPS = 30


def _ff(*args: str) -> None:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def load_docs(out: Path, players: list[str]) -> list[dict]:
    docs = []
    for p in players:
        f = Path(out) / "samples" / f"{p}.json"
        if not f.exists():
            raise FileNotFoundError(f"{f}: no samples (run without --render-only first)")
        docs.append(json.loads(f.read_text()))
    return docs


def date_check(docs: list[dict], start: dict) -> int:
    """Largest gap in days between a sample's engine date and the tick-derived display date."""
    worst = 0
    for d in docs:
        for s in d["samples"]:
            got = display_date(s["t"], start["start_ticks"], start["start_ymd"])
            worst = max(worst, abs((got - date.fromisoformat(s["date"])).days))
    return worst


STILLS = (("start", None), ("1963", "1963-01-01"), ("1966", "1966-01-01"), ("runoff", "1970-07-01"), ("end", None))


def render_theme(out: Path, docs: list[dict], start: dict, theme: str, seconds: float, info: dict) -> list[Path]:
    trs = tracks(docs)
    horizon_t = max(d["horizon_ticks"] for d in docs)
    end_t = max(tr.ts[-1] for tr in trs)
    scene = Scene(trs, Path(out) / "base" / "minimap.png", start, horizon_t, end_t, theme, info)
    n = round(seconds * FPS)
    sched = schedule(n, start["start_ticks"], end_t, hold_start=FPS, hold_end=4 * FPS)
    stills = {}
    for label, ymd in STILLS:
        if ymd is None:
            stills[0 if label == "start" else n - 1] = label
        else:
            target = start["start_ticks"] + (date.fromisoformat(ymd) - date.fromisoformat(start["start_ymd"])).days * TICKS_PER_DAY
            stills[frame_of(target, sched)] = label
    main = Path(out) / f"timelapse-{theme}.mp4"
    proc = subprocess.Popen([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba",
                             "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset", "medium",
                             "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(main)],
                            stdin=subprocess.PIPE)
    made = [main]
    t_start = time.monotonic()
    try:
        for f, now in enumerate(sched):
            buf = scene.render(now)
            proc.stdin.write(bytes(buf))
            if f in stills:
                p = Path(out) / "stills" / f"{theme}-{stills[f]}.png"
                p.parent.mkdir(parents=True, exist_ok=True)
                _png(buf, p)
                made.append(p)
    finally:
        proc.stdin.close()
        proc.wait()
        scene.close()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({proc.returncode}) on {main}")
    print(json.dumps({"theme": theme, "frames": n, "seconds": round(time.monotonic() - t_start, 1)}), flush=True)
    short = Path(out) / f"timelapse-15s-{theme}.mp4"
    _ff("-i", str(main), "-vf", "setpts=PTS/4,fps=30", "-c:v", "libx264",
        "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(short))
    made.append(short)
    return made


def _png(buf: memoryview, path: Path) -> None:
    import matplotlib.image as mpimg
    import numpy as np

    mpimg.imsave(path, np.asarray(buf).reshape(H, W, 4)[..., :3].copy())


def loops(out: Path, theme: str = "dark") -> list[Path]:
    """README-sized loops from the 15-second cut: VP9 WebM and a 15 fps GIF, both 1280 px wide."""
    short = Path(out) / f"timelapse-15s-{theme}.mp4"
    webm = Path(out) / "timelapse-loop.webm"
    _ff("-i", str(short), "-vf", "scale=1280:-2:flags=lanczos", "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "34",
        "-row-mt", "1", "-an", str(webm))
    gif = Path(out) / "timelapse-loop.gif"
    _ff("-i", str(short), "-vf", "fps=15,scale=1280:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128"
        ":stats_mode=diff[p];[b][p]paletteuse=dither=sierra2_4a:diff_mode=rectangle", "-loop", "0", str(gif))
    return [webm, gif]


def render_all(out: Path, players: list[str], task_dir: Path, themes: list[str] = ("dark", "light"),
               seconds: float = 60.0, title: str = "Arena") -> list[Path]:
    out = Path(out)
    task = json.loads((Path(task_dir) / "task.json").read_text())
    info = {"title": title, "task": task["task_id"], "horizon_days": task["horizon_days"],
            "w_idle": task["objective"]["w_idle"]}
    start = json.loads((out / "base" / "probe.json").read_text())
    docs = load_docs(out, players)
    bad = [d["player"] for d in docs if not d["check"]["ok"]]
    if bad:
        print(json.dumps({"warning": "replay check failed, rendering anyway", "players": bad}), file=sys.stderr)
    print(json.dumps({"date_check_max_days": date_check(docs, start)}), flush=True)
    made: list[Path] = []
    for theme in themes:
        made += render_theme(out, docs, start, theme, seconds, info)
    if "dark" in themes:
        made += loops(out, "dark")
    return made


# ============================================================================================ cli
def _opt(argv: list[str], name: str, default: str | None = None) -> str | None:
    return argv[argv.index(name) + 1] if name in argv else default


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-") or "--task" not in argv:
        print(__doc__, file=sys.stderr)
        return 2
    arena_dir, task_dir = Path(argv[0]), Path(_opt(argv, "--task"))
    out = Path(_opt(argv, "--out", "out/timelapse"))
    expected_path = _opt(argv, "--expected")
    expected_doc = json.loads(Path(expected_path).read_text()) if expected_path else None
    players = _opt(argv, "--players")
    names = players.split(",") if players else players_of(arena_dir)
    rc = 0
    if "--render-only" not in argv:
        rows = sample_all(arena_dir, task_dir, out, names, jobs=int(_opt(argv, "--jobs", "4")),
                          expected_doc=expected_doc, resample="--resample" in argv, control="--control" in argv)
        bad = [r["player"] for r in rows if not r.get("cached") and not r.get("ok")]
        print(json.dumps({"sampled": len(rows), "failed_checks": bad}))
        rc = 1 if bad else 0
    if "--sample-only" not in argv:
        made = render_all(out, names, task_dir, themes=_opt(argv, "--theme", "dark,light").split(","),
                          seconds=float(_opt(argv, "--seconds", "60")), title=_opt(argv, "--title", "Arena"))
        for p in made:
            print(p)
    return rc
