"""Cinema stage 2 (matplotlib + ffmpeg, no engine): frames from the samples.

Cuts (each in the dark and the light theme):

* ``split``: the main cut, 1920x1080 at 30 fps. Two arms of one seed side by side, synchronised by game
  date: the same world and the same agents, enforced against cheap talk. With ``--4k`` also a 3840x2160
  master of it. Needs two sampled runs.
* ``main``: one arm full screen with the leaderboard and the camera's slow zooms on key moments (one
  video per arm).
* ``social``: the split screen in 15 seconds (re-rendered, not sped up: captions stay readable).
* ``square``: 1080x1080, the split screen stacked for a feed.
* ``stills``: PNGs of key moments of each cut.

On screen a route or a station appears at the first sample where it exists and stays until a sample no
longer has it (a step function, never ahead of the replay); money is interpolated linearly between
samples; contract events (licences, instalments, breaks, tenders, messages) are drawn at their exact
ticks from the replay's exchange state.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
from datetime import date
from pathlib import Path

from railhead.cinema import story
from railhead.figures import money
from railhead.render import pixel
from railhead.timelapse import FFMPEG, FPS, Box, at_or_before, display_date, money_at, quiet_base

# ============================================================================================ look
FONT = ["Avenir Next", "Avenir", "Helvetica Neue", "Helvetica", "DejaVu Sans"]
FONT_NUM = ["DIN Alternate", "Avenir Next", "Helvetica Neue", "DejaVu Sans"]

THEME = {
    "dark": {"bg": "#07090d", "panel": "#0d1117", "ink": "#eef1f5", "ink2": "#9aa4b2", "ink3": "#5b6575",
             "rule": "#1c2330", "base_land": "#1b222c", "base_water": "#06090e", "base_town": "#3a4454",
             "grid": "#1a212b", "caption_bg": "#0b0f15", "glow": 1.0,
             "paid": "#7dffb2", "collected": "#bfe3ff", "late": "#ffb020", "broken": "#ff3b4a",
             "companies": ["#4da3ff", "#b48cff", "#2ed3b7", "#ff7ac6", "#9be15d", "#6ee7f9", "#f5d76e", "#c7c9ff"]},
    "light": {"bg": "#f4f2ee", "panel": "#fbfaf8", "ink": "#101419", "ink2": "#4f5a68", "ink3": "#8c96a3",
              "rule": "#dedad3", "base_land": "#ebe8e1", "base_water": "#c9d6e2", "base_town": "#c4c0b6",
              "grid": "#e0ddd6", "caption_bg": "#ffffff", "glow": 0.55,
              "paid": "#0f9d58", "collected": "#2b6cb0", "late": "#c77700", "broken": "#d7263d",
              "companies": ["#1f6fd1", "#7a52e0", "#0e9c86", "#d63e95", "#5e9e1f", "#1597b0", "#b08900", "#5a5fd6"]},
}

ARM_LABEL = {"enforced": "ENFORCED", "cheap_talk": "CHEAP TALK"}
ARM_LINE = {"enforced": "The exchange collects every instalment when it falls due.",
            "cheap_talk": "Payments are voluntary. A promise is only words."}


def pretty_model(vendor: str | None, model: str | None) -> str:
    """'claude-opus-5-5' -> 'Claude Opus 5.5'; Codex models keep their id after 'Codex'."""
    m = str(model or vendor or "?")
    if m.startswith("claude-"):
        parts = m[len("claude-"):].split("-")
        words = [p for p in parts if not p.isdigit()]
        nums = [p for p in parts if p.isdigit()]
        return "Claude " + " ".join(w.capitalize() for w in words) + (" " + ".".join(nums) if nums else "")
    if vendor == "codex" or m.startswith("gpt"):
        return f"Codex {m}"
    return m


def rank_order(values: dict[str, float], prev: dict[str, int], hysteresis: float) -> list[str]:
    """Companies by value, highest first. A company keeps its previous rank against a neighbour that is
    within ``hysteresis`` of it, so two near-equal bars do not swap back and forth every frame."""
    if not prev:
        return sorted(values, key=lambda c: -values[c])
    return sorted(values, key=lambda c: -(values[c] - hysteresis * prev.get(c, len(values))))


def rect_px(rect: list[int], size_x: int) -> tuple[float, float, float, float]:
    """Tile rectangle (both corners included) -> minimap pixel box (left, top, width, height); x mirrored."""
    x0, y0, x1, y1 = rect
    left = size_x - 1 - x1 - 0.5
    return left, y0 - 0.5, x1 - x0 + 1, y1 - y0 + 1


def stations_outside(sample: dict, owner_rects: dict[str, list[list[int]]], lic_rects: dict[str, list[list[int]]]) -> int:
    """Stations that lie outside their owner's region and every licence rectangle the owner holds: the
    overlay check (a flipped axis would put most stations outside)."""
    bad = 0
    for cid, c in sample["co"].items():
        rects = owner_rects.get(cid, []) + lic_rects.get(cid, [])
        for _sid, x, y in c["st"]:
            if not any(r[0] <= x <= r[2] and r[1] <= y <= r[3] for r in rects):
                bad += 1
    return bad


# ============================================================================================ data
class Arm:
    """One sampled run, ready for per-frame lookups."""

    def __init__(self, doc: dict) -> None:
        from railhead.multi.regions import RegionMap

        self.doc = doc
        self.arm = doc["arm"]
        self.base = doc["base"]
        self.size_x, self.size_y = int(self.base["size_x"]), int(self.base["size_y"])
        self.comps = doc["companies"]
        self.ids = [str(c["id"]) for c in self.comps]
        self.slot = {str(c["id"]): int(c["slot"]) for c in self.comps}
        self.letter = {str(c["id"]): c["letter"] for c in self.comps}
        self.samples = doc["samples"]
        self.ts = [s["t"] for s in self.samples]
        self.score = {cid: [s["co"][cid]["nw"] + s["co"][cid]["ledger"] for s in self.samples] for cid in self.ids}
        self.regions = RegionMap.from_json(doc["regions"]) if doc.get("regions") else None
        ex = doc.get("exchange") or {"contracts": {}, "obligations": {}, "messages": [], "ledger": {}}
        self.events = story.events_of(ex)
        self.lics = story.licences_of(ex, self.region_rects)
        self.industries = {int(k): v for k, v in (doc.get("industries") or {}).items()}

    def region_rects(self, cid: str, tick: float) -> list[list[int]]:
        if self.regions is None or cid not in self.slot:
            return []
        return self.regions.rects_at(self.slot[cid], int(tick))

    def home(self, cid: str, tick: float = 0) -> tuple[float, float]:
        """A company's anchor on the map, in minimap pixels: the centre of its region."""
        rects = self.region_rects(cid, tick)
        if rects:
            x, y = story.region_centre(rects)
        else:
            x, y = self.size_x / 2, self.size_y / 2
        return pixel(x, y, self.size_x)

    def sample_at(self, t: float) -> dict:
        return self.samples[at_or_before(self.ts, t)]

    def key_ticks(self) -> list[int]:
        firsts, seen = [], set()
        for e in self.events:
            k = {"licence_signed": "s", "broken": "b", "partial": "b", "amended": "a", "late": "l",
                 "plain_late": "l", "collected": "p", "paid": "p", "tender_won": "t"}.get(e.kind)
            if k and k not in seen and e.b != story.EXCHANGE:
                seen.add(k)
                firsts.append(e.t)
        return firsts


def load_arms(out: Path, arms: list[str]) -> list[Arm]:
    docs = []
    for a in arms:
        f = Path(out) / "samples" / f"{a}.json"
        if not f.exists():
            raise FileNotFoundError(f"{f}: no samples (run without --render-only first)")
        d = json.loads(f.read_text())
        if not d["check"]["ok"]:
            raise RuntimeError(f"{f}: the replay check failed ({d['check']['mismatches'][:3]}); not rendering")
        docs.append(d)
    order = {"enforced": 0, "cheap_talk": 1}
    docs.sort(key=lambda d: order.get(d["arm"], 9))
    return [Arm(d) for d in docs]


# ============================================================================================ layout
def layout(kind: str, width: int, height: int, panels: int) -> dict:
    """Pixel boxes (from the top left). ``split``/``square``: one column per arm; ``main``: one arm, the
    map on the left and the HUD column on the right."""
    g = 32 if width >= 1500 else 22
    if kind == "main":
        side = height - 2 * 44
        mapb = Box(g, 44, side, side)
        rx = mapb.x + side + 40
        col = Box(rx, 44, width - rx - g, side)
        return {"kind": kind, "header": Box(rx, 44, col.w, 170), "panels": [{
            "strip": None, "map": mapb, "bars": Box(rx, 236, col.w, 360),
            "counters": Box(rx, 626, col.w, 170), "caption": Box(rx, 830, col.w, side + 44 - 830)}]}
    head = 96 if width >= 1500 else 92
    pw = (width - 2 * g - g * (panels - 1)) / panels
    out = {"kind": kind, "header": Box(g, 0, width - 2 * g, head), "panels": []}
    for i in range(panels):
        x = g + i * (pw + g)
        if kind == "split":
            strip = Box(x, head + 8, pw, 40)
            side = round(min(pw - 170, height - (head + 56) - 168))
            mapb = Box(x, head + 56, side, side)
            counters = Box(x + side + 22, mapb.y, pw - side - 22, side)
            bars = Box(x, mapb.y + side + 16, pw, height - (mapb.y + side + 16) - 14)
        else:  # square
            strip = Box(x, head + 4, pw, 50)
            side = round(pw)
            mapb = Box(x, head + 58, side, side)
            bars = Box(x, mapb.y + side + 14, pw, 168)
            counters = Box(x, bars.y + bars.h + 6, pw, 80)
        caption = Box(mapb.x + 14, mapb.y + mapb.h - 120, mapb.w - 28, 106)
        if kind == "square":  # under the counters, not over the small maps
            caption = Box(x, counters.y + counters.h + 4, pw, height - (counters.y + counters.h + 4) - 12)
        out["panels"].append({"strip": strip, "map": mapb, "counters": counters, "bars": bars, "caption": caption})
    return out


# ============================================================================================ scene
class Scene:
    """One cut: static artists drawn once per frame from scratch (no blitting: the camera moves the map)."""

    def __init__(self, arms: list[Arm], kind: str, theme: str, width: int, height: int, scale: float,
                 sched: list[float], captions: list[list[story.Caption]], base_png: Path, quotes: bool = True) -> None:
        import logging

        import matplotlib

        matplotlib.use("Agg")
        logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
        import matplotlib.image as mpimg
        import matplotlib.pyplot as plt

        self.plt = plt
        self.arms, self.kind, self.sched, self.caps = arms, kind, sched, captions
        self.W, self.H, self.scale = width, height, scale
        self.t = t = THEME[theme]
        self.theme = theme
        self.quotes = quotes
        plt.rcParams.update({"font.family": FONT, "text.color": t["ink"], "figure.facecolor": t["bg"],
                             "savefig.facecolor": t["bg"], "axes.facecolor": t["bg"]})
        self.fig = fig = plt.figure(figsize=(width / 100, height / 100), dpi=100 * scale)
        self.L = L = layout(kind, width, height, len(arms))
        self.base = quiet_base(mpimg.imread(base_png)[..., :3], t)
        self.tmp: list = []
        self.ev_frames = [story.frames_of(a.events, sched) for a in arms]
        self.rank_pos: list[dict[str, float]] = [{} for _ in arms]
        top = max(max(max(v) for v in a.score.values()) for a in arms)
        low = min(0, min(min(min(v) for v in a.score.values()) for a in arms))
        self.money_lim = (low, top)
        self.col = {}
        for a in arms:
            for cid in a.ids:
                self.col[cid] = t["companies"][a.slot[cid] % len(t["companies"])]
        self.panels = []
        for k, a in enumerate(arms):
            P = L["panels"][k]
            ax = fig.add_axes(P["map"].frac(width, height))
            ax.set_facecolor(t["base_water"])
            ax.imshow(self.base, interpolation="nearest", extent=(-0.5, a.size_x - 0.5, a.size_y - 0.5, -0.5),
                      zorder=0)
            self._grid(ax, a)
            self._vignette(ax, a)
            self._regions(ax, a)
            ax.set_xlim(-0.5, a.size_x - 0.5)
            ax.set_ylim(a.size_y - 0.5, -0.5)
            ax.set_xticks([])
            ax.set_yticks([])
            for s in ax.spines.values():
                s.set_color(t["rule"])
                s.set_linewidth(1.0)
            self.panels.append({"ax": ax})
        self._static_hud()

    # ------------------------------------------------------------------------------------ helpers
    def text(self, x: float, y: float, s: str, **kw):
        """Figure text at layout pixels from the top left; agent text never goes through mathtext."""
        kw.setdefault("parse_math", False)
        return self.fig.text(x / self.W, 1 - y / self.H, s, **kw)

    def live_text(self, x: float, y: float, s: str, **kw):
        a = self.text(x, y, s, **kw)
        self.tmp.append(a)
        return a

    def rect(self, b: Box, color: str, alpha: float = 1.0, live: bool = False, z: int = 0, **kw):
        from matplotlib.patches import FancyBboxPatch

        p = FancyBboxPatch((b.x / self.W, 1 - (b.y + b.h) / self.H), b.w / self.W, b.h / self.H,
                           boxstyle=kw.pop("boxstyle", "square,pad=0"), transform=self.fig.transFigure,
                           facecolor=color, edgecolor=kw.pop("ec", "none"), alpha=alpha, zorder=z, **kw)
        self.fig.add_artist(p)
        if live:
            self.tmp.append(p)
        return p

    def _grid(self, ax, a: Arm) -> None:
        for v in range(0, max(a.size_x, a.size_y) + 1, 32):
            ax.axvline(v - 0.5, color=self.t["grid"], linewidth=0.5, alpha=0.6, zorder=1)
            ax.axhline(v - 0.5, color=self.t["grid"], linewidth=0.5, alpha=0.6, zorder=1)

    def _vignette(self, ax, a: Arm) -> None:
        import numpy as np

        n = 64
        yy, xx = np.mgrid[0:n, 0:n] / (n - 1) - 0.5
        r = np.sqrt(xx ** 2 + yy ** 2) / 0.707
        alpha = np.clip((r - 0.55) / 0.45, 0, 1) ** 1.6 * (0.45 if self.theme == "dark" else 0.15)
        bg = self.t["bg"]
        rgb = [int(bg[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        img = np.zeros((n, n, 4))
        img[..., :3] = rgb
        img[..., 3] = alpha
        ax.imshow(img, extent=(-0.5, a.size_x - 0.5, a.size_y - 0.5, -0.5), interpolation="bilinear", zorder=2)

    def _regions(self, ax, a: Arm) -> None:
        from matplotlib.patches import Rectangle

        for cid in a.ids:
            c = self.col[cid]
            rects = a.region_rects(cid, a.doc["start_ticks"])
            for r in rects:
                left, top, w, h = rect_px(r, a.size_x)
                ax.add_patch(Rectangle((left, top), w, h, facecolor=c, alpha=0.045 if self.theme == "dark" else 0.03,
                                       edgecolor="none", zorder=2))
                ax.add_patch(Rectangle((left, top), w, h, facecolor="none", edgecolor=c, alpha=0.55,
                                       linewidth=1.1, linestyle=(0, (4, 3)), zorder=3))
            if rects:
                big = max(rects, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]))
                left, top, w, h = rect_px(big, a.size_x)
                ax.text(left + 3, top + 2.5, a.letter[cid], color=c, alpha=0.75, fontsize=19 if self.kind != "square"
                        else 13, fontweight="bold", va="top", ha="left", zorder=4, parse_math=False)

    # ------------------------------------------------------------------------------------ static HUD
    def _static_hud(self) -> None:
        t, L = self.t, self.L
        hb = L["header"]
        sq = self.kind == "square"
        if self.kind == "main":
            a = self.arms[0]
            self.text(hb.x, hb.y + 6, "W A Y B I L L", fontsize=15, fontweight="bold", va="top", color=t["ink"])
            self.text(hb.x, hb.y + 34, "AI-run freight companies in one OpenTTD world,\ntrading through binding contracts.",
                      fontsize=15, va="top", color=t["ink2"], linespacing=1.3)
            self._arm_pill(hb.x, hb.y + 120, a.arm, 15)
            self.text(hb.x + 190, hb.y + 127, ARM_LINE.get(a.arm, ""), fontsize=12.5, va="top", color=t["ink2"])
            self._legend_row(L["panels"][0]["caption"])
        else:
            self.text(hb.x, hb.y + (22 if not sq else 18), "W A Y B I L L", fontsize=16 if not sq else 13,
                      fontweight="bold", va="top", color=t["ink"])
            sub = ("The same world, the same AI agents, one rule changed: are promises enforced?" if not sq
                   else "Same world, same agents.\nOne rule changed.")
            self.subtitle_default = sub  # drawn per frame: the end card replaces it
            for k, a in enumerate(self.arms):
                P = L["panels"][k]
                s = P["strip"]
                self._arm_pill(s.x, s.y + 8, a.arm, 13 if not sq else 11)
                self.text(s.x + (150 if not sq else 116), s.y + (11 if not sq else 10), ARM_LINE.get(a.arm, ""),
                          fontsize=12.5 if not sq else 9.5, va="top", color=t["ink2"], wrap=False)
                if not sq:
                    self._legend(P["counters"])

    def _legend(self, b: Box, fs: float = 10.5) -> None:
        """What the marks mean, drawn with the marks themselves (in the ink colours)."""
        from matplotlib.lines import Line2D
        from matplotlib.patches import Rectangle

        t = self.t
        rows = [("route", "line"), ("contract arc", "arc"), ("licensed site", "rect"), ("payment", "pulse"),
                ("broken promise", "x"), ("tender", "diamond")]
        y0 = b.y + b.h - len(rows) * 24
        self.text(b.x, y0 - 22, "Key", fontsize=fs, fontweight="bold", va="top", color=t["ink3"])
        for i, (label, mark) in enumerate(rows):
            y = y0 + i * 24 + 8
            x0, x1 = b.x / self.W, (b.x + 26) / self.W
            yy = 1 - y / self.H
            kw = {"transform": self.fig.transFigure, "figure": self.fig}
            if mark == "line":
                self.fig.lines.append(Line2D([x0, x1], [yy, yy], color=t["ink2"], linewidth=2, **kw))
            elif mark == "arc":
                self.fig.lines.append(Line2D([x0, x1], [yy, yy], color=t["ink2"], linewidth=1, linestyle=(0, (3, 2)),
                                             **kw))
            elif mark == "rect":
                self.fig.patches.append(Rectangle((x0, yy - 6 / self.H), 26 / self.W, 12 / self.H, facecolor=t["ink2"],
                                                  alpha=0.25, edgecolor=t["ink2"], linewidth=1, **kw))
            else:
                m, c = {"pulse": ("o", t["paid"]), "x": ("x", t["broken"]), "diamond": ("D", t["ink2"])}[mark]
                self.fig.lines.append(Line2D([(x0 + x1) / 2], [yy], marker=m, markersize=6, color=c,
                                             markeredgewidth=1.8 if m == "x" else 0.5, linestyle="", **kw))
            self.text(b.x + 36, y, label, fontsize=fs, va="center", color=t["ink2"])

    def _legend_row(self, b: Box) -> None:
        """The key on one line at the bottom of ``b`` (the full-screen cut)."""
        sub = Box(b.x + b.w - 160, b.y + b.h - 150, 160, 150)
        self._legend(sub, fs=10)

    def _arm_pill(self, x: float, y: float, arm: str, fs: float) -> None:
        t = self.t
        label = ARM_LABEL.get(arm, arm.upper())
        w = fs * 0.78 * len(label) + 22
        h = fs * 1.9
        col = t["collected"] if arm == "enforced" else t["late"]
        self.rect(Box(x, y, w, h), col, alpha=0.16, boxstyle="round,pad=0,rounding_size=0.006")
        self.rect(Box(x, y, 3, h), col, alpha=0.95)
        self.text(x + 12, y + h / 2, label, fontsize=fs, fontweight="bold", va="center", color=col)

    # ------------------------------------------------------------------------------------ per frame
    def clear(self) -> None:
        for a in self.tmp:
            a.remove()
        self.tmp = []

    def frame(self, f: int) -> memoryview:
        self.clear()
        now = self.sched[f]
        t = self.t
        a0 = self.arms[0]
        d = display_date(now, a0.doc["start_ticks"], a0.base["start_ymd"])
        hb = self.L["header"]
        sq = self.kind == "square"
        if self.kind == "main":
            self.live_text(hb.x + hb.w, hb.y + 2, str(d.year), fontsize=44, fontweight="bold", ha="right", va="top",
                           color=t["ink"], family=FONT_NUM)
            self.live_text(hb.x + hb.w, hb.y + 66, d.strftime("%B"), fontsize=16, ha="right", va="top",
                           color=t["ink2"])
            self.live_text(hb.x + hb.w, hb.y + 96, self._phase(now, a0), fontsize=12, ha="right", va="top",
                           color=t["ink2"])
        else:
            x = hb.x + hb.w
            ha = "right"
            self.live_text(x, hb.y + (12 if not sq else 10), f"{d.strftime('%b')} {d.year}", fontsize=38 if not sq
                           else 26, fontweight="bold", ha=ha, va="top", color=t["ink"], family=FONT_NUM)
            self.live_text(x, hb.y + (62 if not sq else 46), self._phase(now, a0), fontsize=12 if not sq else 10,
                           ha=ha, va="top", color=t["ink2"])
        self.subtitle = getattr(self, "subtitle_default", "")
        self._title_card(f)  # may replace the subtitle
        if self.kind != "main":
            self.live_text(hb.x, hb.y + (50 if not sq else 40), self.subtitle, fontsize=14 if not sq else 11.5,
                           va="top", color=t["ink2"], linespacing=1.25)
        for k, a in enumerate(self.arms):
            self._map(k, a, f, now)
            self._bars(k, a, now)
            self._counters(k, a, now)
            self._caption(k, a, f)
        canvas = self.fig.canvas
        canvas.draw()
        return canvas.buffer_rgba()

    def _phase(self, now: float, a: Arm) -> str:
        h = a.doc["horizon_ticks"]
        if now >= h:
            return "Run-off: the networks run alone" if self.kind == "square" else \
                "Run-off: no more decisions, the networks run alone"
        yrs = a.doc["horizon_days"] / 365
        y = min(math.ceil(yrs), int((now - a.doc["start_ticks"]) / story.TICKS_PER_DAY // 365) + 1)
        return f"Agents in charge: year {y} of {round(yrs)}"

    # ------------------------------------------------------------------------------------ the map
    def _map(self, k: int, a: Arm, f: int, now: float) -> None:
        from matplotlib.collections import LineCollection
        from matplotlib.patches import Circle, Rectangle

        ax = self.panels[k]["ax"]
        t = self.t
        g = t["glow"]
        sq = self.kind == "square"
        if self.kind == "main":
            cx, cy, z = story.camera_at(f, self.caps[k], (a.size_x, a.size_y))
            hx, hy = a.size_x / 2 / z, a.size_y / 2 / z
            ax.set_xlim(cx - hx - 0.5, cx + hx - 0.5)
            ax.set_ylim(cy + hy - 0.5, cy - hy - 0.5)
            zoom = z
        else:
            zoom = 1.0
        px_per_tile = self.L["panels"][k]["map"].w / a.size_x * zoom
        lw_k = max(0.55, px_per_tile / 3.0)
        s = a.sample_at(now)

        def add(art):
            self.tmp.append(art)
            return art

        # networks: one glow stack per company
        for cid in a.ids:
            c = s["co"][cid]
            col = self.col[cid]
            at = {sid: pixel(x, y, a.size_x) for sid, x, y in c["st"]}
            segs, widths = [], []
            for mode, sa, sb, n in c["rt"]:
                if sa not in at or sb not in at:
                    continue
                p0, p1 = at[sa], at[sb]
                if mode == "air":
                    segs.append(story.bezier(p0, p1, 0.18, 16))
                else:
                    segs.append([p0, p1])
                base = {"rail": 1.5, "road": 0.9, "air": 0.8, "water": 1.0}.get(mode, 1.0)
                widths.append(lw_k * base * min(2.2, 0.9 + 0.35 * math.sqrt(n)))
            if segs:
                for mul, alpha in ((5.0, 0.07 * g), (2.6, 0.16 * g), (1.0, 0.95)):
                    lc = LineCollection(segs, colors=col, linewidths=[w * mul for w in widths], alpha=alpha,
                                        capstyle="round", joinstyle="round", zorder=6)
                    add(ax.add_collection(lc))
            if at:
                xs, ys = zip(*at.values(), strict=True)
                add(ax.scatter(xs, ys, s=(lw_k * 3.2) ** 2, color=col, edgecolors=t["bg"], linewidths=0.6 * lw_k,
                               zorder=7))
                if g > 0.6:
                    add(ax.scatter(xs, ys, s=(lw_k * 7) ** 2, color=col, alpha=0.10, linewidths=0, zorder=6))

        # the contract layer
        layer = story.contract_layer(f, now, a.lics, a.events, self.ev_frames[k], fps=FPS)
        arcs = {}
        for lc, inten in layer.glowing:
            col = self.col.get(lc.grantee, t["ink"])
            left, top, w, h = rect_px(lc.rect, a.size_x)
            ghost = now >= lc.end
            fa = min(1.0, inten)
            add(ax.add_patch(Rectangle((left, top), w, h, facecolor=col, alpha=(0.06 if ghost else 0.22) * fa,
                                       edgecolor="none", zorder=8)))
            add(ax.add_patch(Rectangle((left, top), w, h, facecolor="none", edgecolor=col,
                                       alpha=(0.35 if ghost else 0.95) * fa, linewidth=1.3 * lw_k, zorder=9)))
            if not ghost:
                add(ax.add_patch(Rectangle((left, top), w, h, facecolor="none", edgecolor=col,
                                           alpha=0.18 * g * inten, linewidth=6 * lw_k * inten, zorder=8)))
            home = a.home(lc.grantee, lc.start)
            target = (left + w / 2, top + h / 2)
            pts = story.bezier(home, target, 0.22, 32)
            arcs[lc.cid] = pts
            xs, ys = zip(*pts, strict=True)
            if ghost:  # the licence ended: a faint dotted trace keeps its history (and its scars) readable
                add(ax.plot(xs, ys, color=_mix(col, t["ink"], 0.3), alpha=0.38, linewidth=0.8 * lw_k,
                            linestyle=(0, (1, 2.4)), zorder=9)[0])
            else:  # a contract arc: dashed and pale, unlike a route (solid, the company colour)
                add(ax.plot(xs, ys, color=col, alpha=0.10 * g * inten, linewidth=5 * lw_k, solid_capstyle="round",
                            zorder=9)[0])
                add(ax.plot(xs, ys, color=_mix(col, t["ink"], 0.45), alpha=0.75, linewidth=0.9 * lw_k,
                            linestyle=(0, (3, 2.2)), zorder=10)[0])
                add(ax.scatter([home[0]], [home[1]], s=(lw_k * 3.2) ** 2, marker="o", facecolors=t["bg"],
                               edgecolors=col, linewidths=1.0 * lw_k, zorder=10))
        for cid_, kinds in layer.scars.items():
            pts = arcs.get(cid_)
            if pts is None:
                continue
            for i, kind in enumerate(kinds):
                u = 0.5 + (i - (len(kinds) - 1) / 2) * 0.09
                x, y = story.point_on(pts, u)
                col = t["broken"] if kind == "broken" else t["late"]
                add(ax.scatter([x], [y], s=(lw_k * 5.5) ** 2, marker="x", color=col, linewidths=1.6 * lw_k, zorder=12))
        for cid_, kind, strength in layer.flashes:
            pts = arcs.get(cid_)
            col = t["broken"] if kind == "broken" else t["late"]
            if pts is not None:
                xs, ys = zip(*pts, strict=True)
                add(ax.plot(xs, ys, color=col, alpha=0.9 * strength, linewidth=3.2 * lw_k, zorder=11)[0])
                x, y = story.point_on(pts, 0.5)
                r = (1 - strength) * 14 + 2
                add(ax.add_patch(Circle((x, y), r, facecolor="none", edgecolor=col, alpha=strength,
                                        linewidth=2.2 * lw_k, zorder=12)))
        for p in layer.pulses:
            col = t[p.kind] if p.kind in t else t["late"] if "late" in p.kind else t["paid"]
            pts = arcs.get(p.licence) if p.licence else None
            if pts is None:
                pts = story.bezier(a.home(p.a), a.home(p.b), 0.25, 24) if p.b != story.EXCHANGE else None
            if pts is None:
                continue
            fwd = p.licence is None or self._payer_is_grantee(a, p)
            u = story.ease_in_out(p.progress)
            u = u if fwd else 1 - u
            for j, back in enumerate((0.0, 0.04, 0.08, 0.12)):
                x, y = story.point_on(pts, u - back if fwd else u + back)
                size = (lw_k * (7.5 - j * 1.6)) ** 2
                add(ax.scatter([x], [y], s=size * 3.2, color=col, alpha=0.18 * (1 - j / 4), linewidths=0, zorder=13))
                add(ax.scatter([x], [y], s=size * 0.5, color=col if j else "#ffffff", alpha=0.95 * (1 - j / 4),
                               linewidths=0, zorder=14))
        for fa_, fb_, prog, kind in layer.sparks:
            pts = story.bezier(a.home(fa_), a.home(fb_), 0.32, 20)
            x, y = story.point_on(pts, story.ease_in_out(prog))
            col = self.col.get(fa_, t["ink"])
            big = kind == "offer"
            add(ax.scatter([x], [y], s=(lw_k * (4.2 if big else 2.6)) ** 2, color=col, alpha=0.75 * (1 - prog * 0.6),
                           linewidths=0, zorder=13))
        stacked: dict[int, int] = {}
        for e, age, outcome in layer.tenders:
            iid = self._tender_industry(a, e.ref)
            ind = a.industries.get(iid)
            if ind is None:
                continue
            row = stacked[iid] = stacked.get(iid, -1) + 1
            x, y = pixel(ind[0], ind[1], a.size_x)
            col = self.col.get(e.a, t["ink"])
            add(ax.scatter([x], [y], s=(lw_k * 6) ** 2, marker="D", color=col, edgecolors=t["bg"], linewidths=0.8,
                           zorder=12))
            if age < 1:
                add(ax.add_patch(Circle((x, y), 3 + age * 12, facecolor="none", edgecolor=col, alpha=1 - age,
                                        linewidth=2 * lw_k, zorder=12)))
            if outcome:
                oc = {"kept": t["paid"], "partial": t["late"], "broken": t["broken"]}.get(outcome, t["ink2"])
                add(ax.text(x + 3.5, y - 2 + row * (9 if not sq else 12) / zoom, f"{e.ref} {outcome}", color=oc,
                            fontsize=8.5 if not sq else 6.5, fontweight="bold", va="center", zorder=13,
                            parse_math=False))

    @staticmethod
    def _payer_is_grantee(a: Arm, p: story.Pulse) -> bool:
        lc = next((x for x in a.lics if x.cid == p.licence), None)
        return lc is None or lc.grantee == p.a

    @staticmethod
    def _tender_industry(a: Arm, tid: str) -> int:
        tn = ((a.doc.get("exchange") or {}).get("tenders") or {}).get(tid) or {}
        return int(tn.get("industry", -1))

    # ------------------------------------------------------------------------------------ bars
    def _bars(self, k: int, a: Arm, now: float) -> None:
        t = self.t
        b = self.L["panels"][k]["bars"]
        sq = self.kind == "square"
        main = self.kind == "main"
        vals = {cid: money_at(a.ts, a.score[cid], now) for cid in a.ids}
        pos = self.rank_pos[k]
        lo_, hi_ = self.money_lim
        order = rank_order(vals, {c: round(p) for c, p in pos.items()}, 0.015 * (hi_ - lo_))
        for r, cid in enumerate(order):
            pos[cid] = r if cid not in pos else pos[cid] + (r - pos[cid]) * 0.3
            if abs(pos[cid] - r) < 0.02:
                pos[cid] = r
        n = len(a.ids)
        title_h = 26 if not sq else 20
        self.live_text(b.x, b.y + 2, "Score: net worth + ledger" if not main else "Leaderboard: net worth + ledger",
                       fontsize=12 if not sq else 9.5, fontweight="bold", va="top", color=t["ink2"])
        row_h = (b.h - title_h) / n
        name_w = (210 if not sq else 150) if not main else 300
        val_w = 96 if not sq else 70
        lo, hi = self.money_lim
        span = hi - lo or 1
        bar_x0 = b.x + name_w
        bar_w = b.w - name_w - val_w
        zero = bar_x0 + bar_w * (0 - lo) / span
        s = a.sample_at(now)
        for cid in a.ids:
            y = b.y + title_h + pos[cid] * row_h
            c = next(cc for cc in a.comps if str(cc["id"]) == cid)
            col = self.col[cid]
            fs = 12.5 if not sq else 9.5
            if main:
                fs = 17
            self.rect(Box(b.x, y + row_h * 0.5 - 5, 10, 10), col, live=True)
            self.live_text(b.x + 18, y + row_h * 0.5, c["letter"], fontsize=fs, fontweight="bold", va="center",
                           color=col)
            label = pretty_model(c.get("vendor"), c.get("model"))
            self.live_text(b.x + 18 + fs * 1.3, y + row_h * 0.5, label, fontsize=fs * 0.86, va="center",
                           color=t["ink"])
            if main:
                veh = s["co"][cid]["veh"]
                mix = " · ".join(f"{v} {m if m != 'water' else 'ship'}" for m, v in veh.items() if v) or "no vehicles"
                self.live_text(b.x + 18 + fs * 1.3, y + row_h * 0.5 + 22, mix, fontsize=11, va="center",
                               color=t["ink2"])
            v = vals[cid]
            x1 = bar_x0 + bar_w * (v - lo) / span
            bh = min(row_h * 0.52, 26 if not sq else 14)
            left, w = (zero, x1 - zero) if v >= 0 else (x1, zero - x1)
            self.rect(Box(left, y + row_h * 0.5 - bh / 2, max(w, 1.5), bh), col, alpha=0.9, live=True)
            self.live_text(max(x1, zero) + 8, y + row_h * 0.5, money(v), fontsize=fs, va="center", color=t["ink"],
                           family=FONT_NUM)
        self.rect(Box(zero - 0.5, b.y + title_h, 1, b.h - title_h), t["ink3"], alpha=0.6, live=True)

    # ------------------------------------------------------------------------------------ counters
    def _counters(self, k: int, a: Arm, now: float) -> None:
        t = self.t
        b = self.L["panels"][k]["counters"]
        ex = story.counters_at(a.samples, now)
        if not ex:
            return
        enforced = a.arm == "enforced"
        broken = ex.get("obligations_broken", 0) + ex.get("obligations_partial", 0)
        rows = [("Contracts signed", ex.get("signed", 0), t["ink"]),
                ("Licences in force", ex.get("live_licences", 0), t["ink"]),
                ("Instalments collected" if enforced else "Instalments paid on time",
                 ex.get("obligations_collected" if enforced else "obligations_paid_on_time", 0),
                 t["collected"] if enforced else t["paid"]),
                ("Promises broken", broken, t["broken"] if broken else t["ink3"]),
                ("Tenders won", ex.get("tenders_won", 0), t["ink"]),
                ("Messages", ex.get("messages", 0), t["ink2"])]
        if self.kind == "square":
            cw = b.w / 3
            for i, (label, v, col) in enumerate(rows):
                x, y = b.x + (i % 3) * cw, b.y + (i // 3) * 38
                self.live_text(x, y, str(v), fontsize=15, fontweight="bold", va="top", color=col, family=FONT_NUM)
                self.live_text(x + 30, y + 4, label, fontsize=8, va="top", color=t["ink2"])
            return
        if self.kind == "main":
            cw = b.w / 3
            for i, (label, v, col) in enumerate(rows):
                x, y = b.x + (i % 3) * cw, b.y + (i // 3) * 82
                self.live_text(x, y, str(v), fontsize=30, fontweight="bold", va="top", color=col, family=FONT_NUM)
                self.live_text(x, y + 46, label, fontsize=11.5, va="top", color=t["ink2"])
            return
        step = min(92, (b.h - 170) / len(rows))
        for i, (label, v, col) in enumerate(rows):
            y = b.y + 4 + i * step
            self.live_text(b.x, y, str(v), fontsize=28, fontweight="bold", va="top", color=col, family=FONT_NUM)
            self.live_text(b.x, y + 42, label, fontsize=11, va="top", color=t["ink2"])

    # ------------------------------------------------------------------------------------ captions
    def _caption(self, k: int, a: Arm, f: int) -> None:
        got = story.caption_at(self.caps[k], f)
        if got is None:
            return
        c, alpha = got
        t = self.t
        b = self.L["panels"][k]["caption"]
        sq = self.kind == "square"
        main = self.kind == "main"
        quote = c.quote if self.quotes else None
        if main:
            body = _wrap(c.body, 62)
            self.live_text(b.x, b.y + 4, c.title.upper(), fontsize=13, fontweight="bold", va="top",
                           color=self._cap_col(c), alpha=alpha)
            self.live_text(b.x, b.y + 30, body, fontsize=17, va="top", color=t["ink"], alpha=alpha, linespacing=1.3)
            if quote:
                y = b.y + 30 + _px(17) * LS * (1 + body.count("\n")) + 10
                self.live_text(b.x, y, _wrap(f"“{quote}”  — Company {a.letter.get(c.who, '?')}", 70),
                               fontsize=13.5, style="italic", va="top", color=t["ink2"], alpha=alpha, linespacing=1.3)
            return
        fs_t, fs_b, fs_q = (12, 15, 12.5) if not sq else (8.5, 10, 9)
        width_chars = 64 if not sq else 50
        body = _wrap(c.body, width_chars)
        lines = 1 + body.count("\n")
        qtxt = _wrap(f"“{quote}” — {a.letter.get(c.who, '?')}", width_chars + 8) if quote else ""
        qlines = (1 + qtxt.count("\n")) if quote else 0
        pad = 12 if not sq else 8
        h_t, h_b, h_q = _px(fs_t) * 1.25, _px(fs_b) * LS * lines, _px(fs_q) * LS * qlines
        h = pad + h_t + 6 + h_b + (6 + h_q if quote else 0) + pad
        y0 = b.y + b.h - h
        self.rect(Box(b.x, y0, b.w, h), t["caption_bg"], alpha=0.86 * alpha, live=True, z=5)
        self.rect(Box(b.x, y0, 3, h), self._cap_col(c), alpha=alpha, live=True, z=6)
        self.live_text(b.x + 14, y0 + pad, c.title.upper(), fontsize=fs_t, fontweight="bold", va="top",
                       color=self._cap_col(c), alpha=alpha, zorder=7)
        self.live_text(b.x + 14, y0 + pad + h_t + 6, body, fontsize=fs_b, va="top", color=t["ink"], alpha=alpha,
                       linespacing=1.3, zorder=7)
        if quote:
            self.live_text(b.x + 14, y0 + pad + h_t + 6 + h_b + 6, qtxt, fontsize=fs_q, style="italic",
                           va="top", color=t["ink2"], alpha=alpha, linespacing=1.3, zorder=7)

    def _cap_col(self, c: story.Caption) -> str:
        t = self.t
        return {"broken": t["broken"], "partial": t["broken"], "late": t["late"], "plain_late": t["late"],
                "collected": t["collected"], "paid": t["paid"], "horizon": t["ink2"]}.get(c.kind, t["ink"])

    # ------------------------------------------------------------------------------------ title card
    def _title_card(self, f: int) -> None:
        """The opening and closing words over a dimmed frame (the holds at both ends)."""
        n = len(self.sched)
        t = self.t
        intro = 1.6 * FPS
        if f < intro:
            alpha = 1.0 if f < intro * 0.55 else max(0.0, 1 - (f - intro * 0.55) / (intro * 0.45))
            self.rect(Box(0, 0, self.W, self.H), t["bg"], alpha=0.78 * alpha, live=True, z=20)
            sq = self.kind == "square"
            self.live_text(self.W / 2, self.H / 2 - (40 if not sq else 30), "W A Y B I L L", fontsize=54 if not sq else 38,
                           fontweight="bold", ha="center", va="center", color=t["ink"], alpha=alpha, zorder=21)
            line = ("AI agents run rival freight companies in one OpenTTD world"
                    if self.kind != "square" else "AI agents run rival freight\ncompanies in one OpenTTD world")
            self.live_text(self.W / 2, self.H / 2 + 34, line, fontsize=19 if not sq else 15,
                           ha="center", va="center", color=t["ink2"], alpha=alpha, zorder=21, linespacing=1.3)
        tail = min(3.2 * FPS, 0.2 * n)
        if f >= n - tail:
            k = min(1.0, (f - (n - tail)) / (0.6 * FPS))
            self._end_card(k)

    def _end_card(self, k: float) -> None:
        """The payoff: over each map, the final ranking (score = net worth after the run-off + ledger), and the
        provenance line in place of the header subtitle."""
        t = self.t
        sq = self.kind == "square"
        if self.kind != "main":
            self.subtitle = ("Every frame is read from a verified replay of the agents' action logs." if not sq
                             else "Every frame is read from a verified\nreplay of the agents' action logs.")
        for i, a in enumerate(self.arms):
            m = self.L["panels"][i]["map"]
            fin = sorted(a.ids, key=lambda c: -a.score[c][-1])
            w, rh = m.w * 0.78, (44 if not sq else 30)
            h = 70 + rh * len(fin) if not sq else 50 + rh * len(fin)
            box = Box(m.x + (m.w - w) / 2, m.y + (m.h - h) / 2, w, h)
            self.rect(box, t["caption_bg"], alpha=0.9 * k, live=True, z=20)
            fs = 15 if not sq else 10
            self.live_text(box.x + 20, box.y + 16, f"FINAL SCORE · {ARM_LABEL.get(a.arm, a.arm.upper())}",
                           fontsize=fs * 0.85, fontweight="bold", va="top", color=t["ink2"], alpha=k, zorder=21)
            for r, cid in enumerate(fin):
                c = next(cc for cc in a.comps if str(cc["id"]) == cid)
                y = box.y + (54 if not sq else 38) + r * rh + rh / 2
                col = self.col[cid]
                self.live_text(box.x + 20, y, c["letter"], fontsize=fs * 1.3, fontweight="bold", va="center",
                               color=col, alpha=k, zorder=21)
                self.live_text(box.x + 20 + fs * 2.2, y, pretty_model(c.get("vendor"), c.get("model")),
                               fontsize=fs, va="center", color=t["ink"], alpha=k, zorder=21)
                self.live_text(box.x + w - 20, y, money(a.score[cid][-1]), fontsize=fs * 1.2, fontweight="bold",
                               ha="right", va="center", color=t["ink"], alpha=k, zorder=21, family=FONT_NUM)

    def close(self) -> None:
        self.plt.close(self.fig)


LS = 1.3 * 1.2  # matplotlib's line height for linespacing=1.3, in font sizes


def _px(pt: float) -> float:
    """Points -> layout pixels (the figure is 100 layout pixels per inch)."""
    return pt * 100 / 72


def _mix(a: str, b: str, k: float) -> str:
    """Blend two #rrggbb colours: ``k`` of ``b``."""
    ca = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    cb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * k):02x}" for x, y in zip(ca, cb, strict=True))


def _wrap(s: str, width: int) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(s, width)) if s else ""


# ============================================================================================ encode
def schedule_for(arms: list[Arm], seconds: float, hold_start: float = 1.6, hold_end: float = 4.0) -> list[float]:
    a0 = arms[0]
    t0 = a0.doc["start_ticks"]
    t1 = max(a.doc["end_ticks"] for a in arms)
    keys = sorted({k for a in arms for k in a.key_ticks()})
    return story.warp_schedule(round(seconds * FPS), t0, t1, a0.doc["horizon_ticks"], keys,
                               round(hold_start * FPS), round(hold_end * FPS))


def captions_for(arms: list[Arm], sched: list[float], quotes: bool, hold_s: float) -> list[list[story.Caption]]:
    out = []
    for a in arms:
        name = {cid: a.letter[cid] for cid in a.ids}
        caps = story.captions_of(a.events, story.frames_of(a.events, sched), a.lics, name, a.doc["horizon_ticks"],
                                 sched, fps=FPS, quotes=quotes, hold_s=hold_s, arm=a.arm)
        for c in caps:
            if c.focus is not None:
                c.focus = pixel(c.focus[0], c.focus[1], a.size_x)
        out.append(caps)
    return out


def still_frames(arms: list[Arm], sched: list[float], caps: list[list[story.Caption]]) -> dict[int, str]:
    """Key moments: the opening (after the title), the first licence, the first broken promise, a frame
    with the most licences in force, the horizon and the end."""
    from railhead.timelapse import frame_of

    out: dict[int, str] = {}
    n = len(sched)
    out[round(1.7 * FPS)] = "open"
    for cs in caps:
        for c in cs:
            if c.kind in ("licence_signed", "broken", "collected", "paid", "amended"):
                out.setdefault(min(n - 1, c.frame + round(1.2 * FPS)), c.kind)
    a0 = arms[0]
    busiest = max(range(n), key=lambda f: sum(sum(1 for lc in a.lics if lc.start <= sched[f] < lc.end)
                                               for a in arms) + f / n)
    out.setdefault(busiest, "busiest")
    out.setdefault(frame_of(a0.doc["horizon_ticks"] - 30 * story.TICKS_PER_DAY, sched), "horizon")
    out[n - 1] = "end"
    return dict(sorted(out.items()))


def render_cut(out: Path, arms: list[Arm], kind: str, theme: str, seconds: float, *, width: int = 1920,
               height: int = 1080, scale: float = 1.0, name: str | None = None, stills: bool = True,
               quotes: bool = True, still_only: str | None = None) -> list[Path]:
    from railhead.timelapse import _png, frame_of

    out = Path(out)
    hold_s = 3.6 if seconds >= 40 else 2.4
    hold_start = 1.6
    sched = schedule_for(arms, seconds, hold_start=hold_start, hold_end=4.0 if seconds >= 40 else 2.0)
    caps = captions_for(arms, sched, quotes, hold_s)
    scene = Scene(arms, kind, theme, width, height, scale, sched, caps, Path(out) / "base" / "minimap.png", quotes)
    pw, ph = round(width * scale), round(height * scale)
    name = name or f"cinema-{kind}-{theme}"
    made: list[Path] = []
    try:
        if still_only:
            a0 = arms[0]
            target = a0.doc["start_ticks"] + (date.fromisoformat(still_only)
                                             - date.fromisoformat(a0.base["start_ymd"])).days * story.TICKS_PER_DAY
            f = frame_of(target, sched)
            p = out / "stills" / f"{name}-at-{still_only}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            _png(scene.frame(f), p, pw, ph)
            return [p]
        keep = still_frames(arms, sched, caps) if stills else {}
        mp4 = out / f"{name}.mp4"
        proc = subprocess.Popen([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt",
                                 "rgba", "-s", f"{pw}x{ph}", "-r", str(FPS), "-i", "-", "-c:v", "libx264", "-preset",
                                 "medium", "-crf", "16" if scale > 1 else "18", "-pix_fmt", "yuv420p", "-movflags",
                                 "+faststart", str(mp4)], stdin=subprocess.PIPE)
        made.append(mp4)
        t0 = time.monotonic()
        try:
            for f in range(len(sched)):
                buf = scene.frame(f)
                proc.stdin.write(bytes(buf))
                if f in keep:
                    p = out / "stills" / f"{name}-{f:04d}-{keep[f]}.png"
                    p.parent.mkdir(parents=True, exist_ok=True)
                    _png(buf, p, pw, ph)
                    made.append(p)
        finally:
            proc.stdin.close()
            proc.wait()
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed ({proc.returncode}) on {mp4}")
        print(json.dumps({"cut": name, "frames": len(sched), "seconds": round(time.monotonic() - t0, 1)}), flush=True)
    finally:
        scene.close()
    return made


def check_overlay(arms: list[Arm]) -> dict:
    """Stations outside their owner's region and its licences, per arm, at every sample (should be 0)."""
    out = {}
    for a in arms:
        bad = 0
        for s in a.samples:
            own = {cid: a.region_rects(cid, s["t"]) for cid in a.ids}
            lic = {cid: [lc.rect for lc in a.lics if lc.grantee == cid and lc.start <= s["t"]] for cid in a.ids}
            bad += stations_outside(s, own, lic)
        out[a.arm] = bad
    return out


def render_all(out: Path, arms: list[str], themes=("dark", "light"), seconds: float = 60.0,
               cuts=("main", "split", "social", "square", "stills"), master_4k: bool = False, quotes: bool = True,
               still_only: str | None = None) -> list[Path]:
    out = Path(out)
    data = load_arms(out, arms)
    print(json.dumps({"overlay_outside": check_overlay(data)}), flush=True)
    two = len(data) == 2
    stills = "stills" in cuts
    made: list[Path] = []
    for theme in themes:
        if two and "split" in cuts:
            made += render_cut(out, data, "split", theme, seconds, stills=stills, quotes=quotes, still_only=still_only)
            if master_4k and not still_only:
                made += render_cut(out, data, "split", theme, seconds, scale=2.0, name=f"cinema-split-{theme}-4k",
                                   stills=False, quotes=quotes)
        if two and "social" in cuts and not still_only:
            made += render_cut(out, data, "split", theme, 15.0, name=f"cinema-social-15s-{theme}", stills=False,
                               quotes=quotes)
        if two and "square" in cuts:
            made += render_cut(out, data, "square", theme, max(20.0, seconds / 2), width=1080, height=1080,
                               name=f"cinema-square-{theme}", stills=stills, quotes=quotes, still_only=still_only)
        if "main" in cuts:
            for a in data:
                made += render_cut(out, [a], "main", theme, seconds, name=f"cinema-{a.arm}-{theme}", stills=stills,
                                   quotes=quotes, still_only=still_only)
    return made
