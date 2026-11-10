"""Tile cache for the scripted oracles: the ``tiles`` query layers, keyed by (x, y).

A player-side helper (like ``oracle/road.py``'s own loader): it only reads what the ``tiles`` query
shows a player, in 64x64 windows.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

Tile = tuple[int, int]
N4 = ((1, 0), (-1, 0), (0, 1), (0, -1))


@dataclass
class TileInfo:
    kind: str
    height: int
    slope: str   # '0' flat, '1' sloped, 's' steep
    build: bool
    owner: str
    dock: bool = False


class TileMap:
    """Tiles loaded so far. ``call`` is ``World.call`` (or a fake with the same signature)."""

    def __init__(self, call: Callable[..., Any], size_x: int, size_y: int) -> None:
        self.call = call
        self.mx, self.my = size_x, size_y
        self.tiles: dict[Tile, TileInfo] = {}

    @classmethod
    def from_world(cls, world) -> TileMap:
        info = world.call("map_info")
        return cls(world.call, int(info["size_x"]), int(info["size_y"]))

    def idx(self, t: Tile) -> int:
        return t[1] * self.mx + t[0]

    def xy(self, index: int) -> Tile:
        return index % self.mx, index // self.mx

    def get(self, t: Tile) -> TileInfo | None:
        return self.tiles.get(t)

    def load_rows(self, rows: Iterable[dict[str, Any]]) -> None:
        for row in rows:
            y, x0 = int(row["y"]), int(row["x"])
            for i, k in enumerate(row["kind"]):
                self.tiles[(x0 + i, y)] = TileInfo(
                    kind=k, height=int(row["height"][i], 36), slope=row["slope"][i],
                    build=row["build"][i] == "1", owner=row["owner"][i],
                    dock=row.get("dock", "0" * len(row["kind"]))[i] == "1")

    def load_area(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Load the inclusive rectangle, clipped to the map interior."""
        x0, y0 = max(1, x0), max(1, y0)
        x1, y1 = min(self.mx - 2, x1), min(self.my - 2, y1)
        for by in range(y0, y1 + 1, 64):
            for bx in range(x0, x1 + 1, 64):
                w, h = min(64, x1 - bx + 1), min(64, y1 - by + 1)
                self.load_rows(self.call("tiles", x=bx, y=by, w=w, h=h))

    # ------------------------------------------------------------------ water
    def is_water(self, t: Tile) -> bool:
        """Water a ship can sail: flat water or a lock. Sloped river tiles (rapids) stop ships."""
        ti = self.tiles.get(t)
        return bool(ti and ((ti.kind == "~" and ti.slope == "0") or ti.kind == "L"))

    def water_component(self, start: Tile, limit: int = 400_000) -> set[Tile]:
        """Open-water tiles 4-connected to ``start`` (only among loaded tiles)."""
        if not self.is_water(start):
            return set()
        seen = {start}
        q = deque([start])
        while q and len(seen) < limit:
            x, y = q.popleft()
            for dx, dy in N4:
                n = (x + dx, y + dy)
                if n not in seen and self.is_water(n):
                    seen.add(n)
                    q.append(n)
        return seen
