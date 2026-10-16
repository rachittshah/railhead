"""Network map of a live world: the engine's own minimap, with the routes of every vehicle on top.

    railhead render <state_dir> <out.png> [--title T]

The engine draws the minimap (``screenshot minimap`` over the admin port, the "owner" view: water,
land, towns, company property). Railhead then reads the stations and each vehicle's orders through
the daemon, and draws one line for each pair of consecutive stops, as wide as the number of vehicles
that use it. The colour is the transport mode (the same colours as ``railhead figures``). Reads only:
the action log does not change. Needs matplotlib.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from pathlib import Path

from railhead.figures import THEMES, _header, _plt

MODES = ("road", "rail", "air", "water")


def pixel(x: int, y: int, size_x: int) -> tuple[int, int]:
    """Tile -> minimap pixel. The engine mirrors x (screenshot.cpp: col = SizeX - 1 - x, row = y)."""
    return size_x - 1 - x, y


def routes(orders: dict[int, tuple[str, list[dict]]]) -> Counter:
    """(mode, station_a, station_b) with a < b -> number of vehicles whose order loop has that leg."""
    out: Counter = Counter()
    for mode, olist in orders.values():
        stops = [o["station"] for o in olist if o.get("kind") == "station"]
        if len(stops) < 2:
            continue
        legs = {tuple(sorted((a, b))) for a, b in zip(stops, stops[1:] + stops[:1]) if a != b}
        for a, b in legs:
            out[(mode, a, b)] += 1
    return out


def _cfg(state_dir: Path) -> dict[str, str]:
    kv = {}
    for line in (Path(state_dir) / "world" / "openttd.cfg").read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    return kv


def engine_minimap(state_dir: Path, name: str = "railhead_minimap") -> Path:
    from railhead.admin_client import AdminClient

    cfg = _cfg(state_dir)
    shot = Path(state_dir) / "world" / "screenshot" / f"{name}.png"
    shot.unlink(missing_ok=True)
    admin = AdminClient.connect("127.0.0.1", int(cfg["server_admin_port"]), cfg["admin_password"], name="render")
    try:
        admin.rcon(f"screenshot minimap {name}")
    finally:
        admin.close()
    deadline = time.monotonic() + 20
    while not shot.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    if not shot.exists():
        raise RuntimeError(f"the engine did not write {shot}")
    return shot


def network(state_dir: Path) -> tuple[list[dict], dict[int, tuple[str, list[dict]]], dict]:
    from railhead.client import Client

    c = Client(Path(state_dir))
    info = c.request("info")
    stations = c.request("query", m="stations", a={})
    orders = {v["id"]: (v["vtype"], c.request("query", m="orders", a={"vehicle": v["id"]}))
              for v in c.request("query", m="vehicles", a={})}
    return stations, orders, info


def draw(minimap: Path, stations: list[dict], legs: Counter, out: Path, title: str, subtitle: str,
         theme: str = "light") -> Path:
    import matplotlib.image as mpimg
    from matplotlib import patheffects
    from matplotlib.lines import Line2D

    t = THEMES[theme]
    plt = _plt(t)
    import numpy as np

    img = mpimg.imread(minimap)[..., :3]
    surface = np.array([int(t["surface"][i:i + 2], 16) / 255 for i in (1, 3, 5)])
    img = img * 0.5 + surface * 0.5  # recede the terrain: the routes are the data
    size_y, size_x = img.shape[:2]
    fig = plt.figure(figsize=(8, 8.9))
    ax = fig.add_axes((0.02, 0.06, 0.96, 0.96 * 8 / 8.9 - 0.02))
    ax.imshow(img, interpolation="nearest", extent=(-0.5, size_x - 0.5, size_y - 0.5, -0.5))
    at = {s["id"]: pixel(s["x"], s["y"], size_x) for s in stations}
    ring = [patheffects.Stroke(linewidth=0, foreground=t["surface"])]
    for (mode, a, b), n in sorted(legs.items(), key=lambda kv: kv[1]):
        if a in at and b in at:
            w = 0.8 + 0.9 * math.sqrt(n)
            ring[0] = patheffects.Stroke(linewidth=w + 2, foreground=t["surface"])
            ax.plot([at[a][0], at[b][0]], [at[a][1], at[b][1]], color=t[mode], linewidth=w, alpha=0.9,
                    solid_capstyle="round", path_effects=[ring[0], patheffects.Normal()])
    used = {s["id"] for (_, a, b) in legs for s in stations if s["id"] in (a, b)}
    xs = [at[i][0] for i in used]
    ys = [at[i][1] for i in used]
    ax.scatter(xs, ys, s=28, color=t["ink"], edgecolors=t["surface"], linewidths=1.5, zorder=5)
    ax.set_xlim(-0.5, size_x - 0.5)
    ax.set_ylim(size_y - 0.5, -0.5)
    ax.axis("off")
    per_mode = Counter()
    for (mode, _, _), n in legs.items():
        per_mode[mode] += 1
    handles = [Line2D([], [], color=t[m], linewidth=3, label=f"{m}: {per_mode[m]} legs") for m in MODES if per_mode[m]]
    handles.append(Line2D([], [], marker="o", linestyle="", markersize=6, color=t["ink"], label="station in use"))
    fig.legend(handles=handles, loc="lower left", ncol=len(handles), fontsize=9, bbox_to_anchor=(0.02, 0.0))
    _header(fig, title, subtitle, t)
    fig.savefig(out, dpi=130)
    plt.close(fig)
    return Path(out)


def render(state_dir: Path, out: Path, title: str | None = None) -> Path:
    shot = engine_minimap(state_dir)
    stations, orders, info = network(state_dir)
    legs = routes(orders)
    fleet = Counter(mode for mode, _ in orders.values())
    mix = ", ".join(f"{n} {m}" for m, n in fleet.most_common())
    sub = (f"{info['task_id']} on {info['date']}: {len(orders)} vehicles ({mix}), "
           f"{len(stations)} stations. Line width: vehicles on that leg.")
    return draw(shot, stations, legs, Path(out), title or "Route network", sub)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    title = argv[argv.index("--title") + 1] if "--title" in argv else None
    print(render(Path(argv[0]), Path(argv[1]), title))
    return 0
