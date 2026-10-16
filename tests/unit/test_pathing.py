"""Oracle-side road planning (the oracle is a scripted player; models get no pathfinder)."""

from railhead.oracle.pathing import Grid, astar, segments


def grid_from(rows: list[str]) -> Grid:
    """'.' clear, '=' existing road, '#' blocked, '/' slope."""
    cost = {".": 1.0, "=": 0.3, "/": 2.0}
    h, w = len(rows), len(rows[0])
    return Grid(w, h, lambda x, y: cost.get(rows[y][x]))


def test_straight_path_on_open_ground():
    g = grid_from(["....."] * 3)
    path = astar(g, (0, 1), (4, 1))
    assert path == [(0, 1), (1, 1), (2, 1), (3, 1), (4, 1)]


def test_path_routes_around_obstacles():
    g = grid_from([
        ".....",
        ".###.",
        ".....",
    ])
    path = astar(g, (0, 1), (4, 1))
    assert path[0] == (0, 1) and path[-1] == (4, 1)
    assert all(g.cost(x, y) is not None for x, y in path)
    assert len(path) == 7


def test_path_prefers_existing_road():
    g = grid_from([
        ".....",
        "=====",
        ".....",
    ])
    path = astar(g, (0, 0), (4, 0))
    assert all(y in (0, 1) for _, y in path)
    assert sum(1 for x, y in path if y == 1) >= 3


def test_unreachable_returns_none():
    g = grid_from(["..#..", "..#..", "..#.."])
    assert astar(g, (0, 0), (4, 0)) is None


def test_segments_compress_to_straight_drags():
    path = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (3, 2)]
    assert segments(path) == [((0, 0), (2, 0)), ((2, 0), (2, 2)), ((2, 2), (3, 2))]
