"""Deterministic A* road planner over a tile grid (4-neighbour, like OpenTTD road pieces)."""

from __future__ import annotations

import heapq
from collections.abc import Callable
from dataclasses import dataclass

Tile = tuple[int, int]


@dataclass
class Grid:
    width: int
    height: int
    cost_fn: Callable[[int, int], float | None]  # None = impassable

    def cost(self, x: int, y: int) -> float | None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return None
        return self.cost_fn(x, y)


def astar(grid: Grid, start: Tile, goal: Tile, turn_penalty: float = 0.5) -> list[Tile] | None:
    """Cheapest 4-connected path; ties broken deterministically. Turns cost extra (fewer drags)."""
    if grid.cost(*start) is None or grid.cost(*goal) is None:
        return None

    def h(t: Tile) -> float:
        return 0.3 * (abs(t[0] - goal[0]) + abs(t[1] - goal[1]))  # admissible: min tile cost is 0.3

    # state = (tile, incoming direction) so turn penalties are exact
    start_state = (start, (0, 0))
    open_heap: list[tuple[float, float, int, Tile, Tile]] = [(h(start), 0.0, 0, start, (0, 0))]
    best: dict[tuple[Tile, Tile], float] = {start_state: 0.0}
    parent: dict[tuple[Tile, Tile], tuple[Tile, Tile] | None] = {start_state: None}
    counter = 0
    while open_heap:
        _, g, _, tile, d = heapq.heappop(open_heap)
        if tile == goal:
            out = []
            s: tuple[Tile, Tile] | None = (tile, d)
            while s is not None:
                out.append(s[0])
                s = parent[s]
            return out[::-1]
        if g > best.get((tile, d), float("inf")):
            continue
        for nd in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nt = (tile[0] + nd[0], tile[1] + nd[1])
            c = grid.cost(*nt)
            if c is None:
                continue
            ng = g + c + (turn_penalty if d != (0, 0) and nd != d else 0.0)
            key = (nt, nd)
            if ng < best.get(key, float("inf")):
                best[key] = ng
                parent[key] = (tile, d)
                counter += 1
                heapq.heappush(open_heap, (ng + h(nt), ng, counter, nt, nd))
    return None


def segments(path: list[Tile]) -> list[tuple[Tile, Tile]]:
    """Compress a 4-connected path into straight runs (each one GUI-style drag)."""
    if len(path) < 2:
        return []
    out = []
    seg_start = path[0]
    direction = (path[1][0] - path[0][0], path[1][1] - path[0][1])
    for prev, cur in zip(path[1:], path[2:]):
        d = (cur[0] - prev[0], cur[1] - prev[1])
        if d != direction:
            out.append((seg_start, prev))
            seg_start, direction = prev, d
    out.append((seg_start, path[-1]))
    return out
