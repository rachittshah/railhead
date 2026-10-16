"""Charts of arena results for the documents: SVG, a light and a dark version of each chart.

    railhead figures docs/data/arena2.json [docs/data/arena1.json ...] [--out docs/img]

The input is the file that ``railhead arena <dir> --export`` writes. Each input gives three charts:
``<task>-networth`` (net worth after the run-off), ``<task>-growth`` (net worth during the mandate, one
panel per player) and ``<task>-fleet`` (vehicles bought, by transport mode). Colours: the vendor is
the identity in the first two charts, the transport mode in the third (validated palettes, see
docs/img/README.md). Needs matplotlib (``uv pip install matplotlib``).
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "grid": "#e6e5e1", "muted": "#c9c8c1",
              "claude": "#2a78d6", "openai": "#eb6834", "road": "#1baf7a", "rail": "#eda100", "air": "#e87ba4",
              "water": "#6250d6"},
    "dark": {"surface": "#1a1a19", "ink": "#f0efec", "ink2": "#c3c2b7", "grid": "#33332f", "muted": "#4a4944",
             "claude": "#3987e5", "openai": "#d95926", "road": "#199e70", "rail": "#c98500", "air": "#d55181",
             "water": "#9085e9"},
}
VENDOR_LABEL = {"claude": "Claude (Claude Code)", "openai": "OpenAI (Codex)"}
BASELINE_LABEL = {"baseline": "scripted truck route (baseline)", "idle": "no play"}
MODES = ("road", "rail", "air", "water")


def money(v: float) -> str:
    sign, a = ("−" if v < 0 else ""), abs(v)
    for unit, div in (("M", 1e6), ("k", 1e3)):
        if a >= div:
            x = a / div
            return f"{sign}£{x:.{0 if x >= 100 else 1 if x >= 10 else 2}f}{unit}"
    return f"{sign}£{a:.0f}"


def vendor(player: str) -> str:
    return "claude" if player.startswith("claude") else "openai"


def _plt(t: dict):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({
        "svg.fonttype": "none", "svg.hashsalt": "railhead", "font.family": ["Helvetica", "Arial", "DejaVu Sans"],
        "font.size": 10, "text.color": t["ink"], "axes.labelcolor": t["ink2"], "xtick.color": t["ink2"],
        "ytick.color": t["ink2"], "axes.edgecolor": t["grid"], "axes.facecolor": t["surface"],
        "figure.facecolor": t["surface"], "savefig.facecolor": t["surface"], "axes.spines.top": False,
        "axes.spines.right": False, "axes.grid": False, "legend.frameon": False,
    })
    return plt


def _header(fig, title: str, subtitle: str, t: dict) -> None:
    fig.text(0.012, 0.985, title, ha="left", va="top", fontsize=13, fontweight="bold", color=t["ink"])
    fig.text(0.012, 0.985 - 0.32 / fig.get_figheight(), subtitle, ha="left", va="top", fontsize=9.5, color=t["ink2"])


def _save(fig, path: Path) -> Path:
    fig.savefig(path, format="svg", metadata={"Date": None})
    import matplotlib.pyplot as plt

    plt.close(fig)
    return path


def _done(doc: dict) -> list[dict]:
    return [r for r in doc["results"] if r["status"] in ("verified", "certificate")]


def _playing_note(doc: dict) -> str:
    live = [r for r in doc["results"] if r["status"] == "playing"]
    return "; ".join(f"{r['player']}: still playing (reached {r['reached']}), not shown" for r in live)


def networth(doc: dict, out: Path, theme: str, title: str) -> Path:
    from matplotlib.patches import Patch

    t = THEMES[theme]
    plt = _plt(t)
    rows = _done(doc)
    fig, ax = plt.subplots(figsize=(8.6, 0.36 * len(rows) + 1.9))
    fig.subplots_adjust(left=0.27, right=0.9, top=1 - 0.95 / fig.get_figheight(), bottom=0.7 / fig.get_figheight())
    top = max(r["net_worth"] for r in rows)
    for i, r in enumerate(rows):
        base = r["status"] == "certificate"
        color = t["muted"] if base else t[vendor(r["player"])]
        ax.barh(i, r["net_worth"] / 1e6, height=0.62, color=color, edgecolor=t["surface"], linewidth=2)
        ax.text(max(r["net_worth"], 0) / 1e6 + top / 1e6 * 0.012, i, money(r["net_worth"]), va="center",
                ha="left", fontsize=9, color=t["ink"])
    ax.set_yticks(range(len(rows)), [BASELINE_LABEL.get(r["player"], r["player"]) for r in rows])
    for lab, r in zip(ax.get_yticklabels(), rows):
        lab.set_color(t["ink2"] if r["status"] == "certificate" else t["ink"])
    ax.invert_yaxis()
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.xaxis.grid(True, color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlabel("net worth (cash − loan) after the run-off year, £ million")
    ax.axvline(0, color=t["ink2"], linewidth=0.8)
    ax.legend(handles=[Patch(color=t["claude"], label=VENDOR_LABEL["claude"]),
                       Patch(color=t["openai"], label=VENDOR_LABEL["openai"]),
                       Patch(color=t["muted"], label="certified reference")], loc="lower right", fontsize=9)
    note = _playing_note(doc)
    sub = f"{doc['task']}: every model bar is the replay of its own action log (proof-of-play), 0 divergences"
    _header(fig, title, sub, t)
    if note:
        fig.text(0.012, 0.012, note, fontsize=8.5, color=t["ink2"], ha="left", va="bottom")
    return _save(fig, out)


def _years(d: str) -> float:
    y, m, dd = (int(x) for x in d.split("-"))
    return y + (date(y, m, dd).timetuple().tm_yday - 1) / 365.25


def growth(doc: dict, out: Path, theme: str, title: str) -> Path:
    t = THEMES[theme]
    plt = _plt(t)
    order = [r["player"] for r in doc["results"] if r["player"] in doc["curves"] and len(doc["curves"][r["player"]]) > 1]
    series = {p: ([_years(s["date"]) for s in doc["curves"][p]], [s["net_worth"] / 1e6 for s in doc["curves"][p]])
              for p in order}
    playing = {r["player"] for r in doc["results"] if r["status"] == "playing"}
    x0 = min(xs[0] for xs, _ in series.values())
    x1 = max(xs[-1] for xs, _ in series.values())
    cols = 4 if len(order) > 6 else max(1, min(3, len(order)))
    nrows = math.ceil(len(order) / cols)
    fig, axes = plt.subplots(nrows, cols, figsize=(2.55 * cols + 0.6, 1.9 * nrows + 1.2), sharex=True, sharey=True,
                             squeeze=False)
    fig.subplots_adjust(left=0.07, right=0.98, top=1 - 0.95 / fig.get_figheight(), bottom=0.55 / fig.get_figheight(),
                        hspace=0.42, wspace=0.12)
    for k, ax in enumerate(axes.flat):
        if k >= len(order):
            ax.set_visible(False)
            continue
        p = order[k]
        for q in order:  # every other player as context
            if q != p:
                ax.plot(*series[q], color=t["muted"], linewidth=1, alpha=0.9)
        xs, ys = series[p]
        ax.plot(xs, ys, color=t[vendor(p)], linewidth=2, solid_capstyle="round")
        early = xs[-1] < (x0 + x1) / 2  # a short series: put the label after its end, not before
        ax.text(xs[-1], ys[-1], " " + money(ys[-1] * 1e6) + " ", fontsize=8.5, color=t["ink"], va="bottom",
                ha="left" if early else "right")
        ax.set_title(p + (" (still playing)" if p in playing else ""), loc="left", fontsize=9.5, color=t["ink"], pad=3)
        ax.yaxis.grid(True, color=t["grid"], linewidth=0.8)
        ax.set_axisbelow(True)
        ax.tick_params(length=0, labelsize=8.5)
    fig.supylabel("net worth (cash − loan), £ million", fontsize=9, color=t["ink2"], x=0.005)
    _header(fig, title, f"{doc['task']}: each panel is one player during the mandate (grey lines: the other players)", t)
    return _save(fig, out)


def fleet(doc: dict, out: Path, theme: str, title: str) -> Path:
    from matplotlib.patches import Patch

    t = THEMES[theme]
    plt = _plt(t)
    order = [r["player"] for r in doc["results"] if r["player"] in doc["fleet_mix"]]
    mix = doc["fleet_mix"]
    used = [m for m in MODES if any(mix[p][m] for p in order)]
    top = max(1, max(sum(mix[p].values()) for p in order))
    fig, ax = plt.subplots(figsize=(8.6, 0.36 * len(order) + 1.9))
    fig.subplots_adjust(left=0.27, right=0.93, top=1 - 0.95 / fig.get_figheight(), bottom=0.7 / fig.get_figheight())
    for i, p in enumerate(order):
        left = 0
        for m in used:
            n = mix[p][m]
            if not n:
                continue
            ax.barh(i, n, left=left, height=0.62, color=t[m], edgecolor=t["surface"], linewidth=2)
            if n >= top * 0.06:
                ax.text(left + n / 2, i, str(n), ha="center", va="center", fontsize=8.5, color="#0b0b0b")
            left += n
        ax.text(left + top * 0.012, i, f"{left} total", va="center", ha="left", fontsize=9, color=t["ink"])
    ax.set_yticks(range(len(order)), order)
    ax.invert_yaxis()
    ax.tick_params(axis="y", length=0)
    ax.spines["left"].set_visible(False)
    ax.xaxis.grid(True, color=t["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    ax.set_xlabel("vehicles bought during the mandate (rail includes wagons)")
    unused = [m for m in MODES if m not in used]
    ax.legend(handles=[Patch(color=t[m], label=m) for m in used], loc="lower right", fontsize=9,
              title=("not used: " + ", ".join(unused)) if unused else None, title_fontsize=8.5)
    _header(fig, title, f"{doc['task']}: players in the order of their net worth; every mode was permitted", t)
    return _save(fig, out)


def build(paths: list[Path], out_dir: Path) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for path in paths:
        doc = json.loads(Path(path).read_text())
        stem = Path(path).stem
        for theme, suffix in (("light", ""), ("dark", "-dark")):
            made.append(networth(doc, out_dir / f"{stem}-networth{suffix}.svg", theme, "Who made the most money"))
            made.append(growth(doc, out_dir / f"{stem}-growth{suffix}.svg", theme, "How fast each company grew"))
            made.append(fleet(doc, out_dir / f"{stem}-fleet{suffix}.svg", theme, "How each company scaled"))
    return made


def main(argv: list[str]) -> int:
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else Path("docs/img")
    paths = [Path(a) for a in argv if a.endswith(".json")]
    for p in build(paths, out):
        print(p)
    return 0
