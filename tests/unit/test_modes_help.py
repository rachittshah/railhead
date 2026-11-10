"""Player-facing help for water and rail: recipes, the dock map view, overview maps, tile details."""

from railhead.cli import RECIPES, help_text
from railhead.observe import format_tile, render_map


def test_rail_and_water_help_end_with_a_recipe_that_uses_real_verbs():
    from railhead.actions import REGISTRY

    for group in ("rail", "water"):
        text = help_text(group)
        assert "RECIPE" in text
        verbs = {f"{g}.{v}" for g, v in (n.split(".") for n in REGISTRY)}
        used = {f"{a}.{b}" for line in RECIPES[group] for a, b in zip(line.split(), line.split()[1:])
                if f"{a}.{b}" in verbs}
        assert used, group
    assert "rh help rail | rh help water" in help_text()


def test_dock_view_marks_dock_tiles_on_the_kind_map():
    rows = [{"y": 5, "x": 10, "kind": ".,~~", "height": "0000", "owner": "....", "slope": "0100",
             "build": "1100", "dock": "0100"}]
    text = render_map(rows, layer="dock")
    assert " .d~~" in text and "d a dock fits here" in text


def test_overview_ruler_labels_real_x_coordinates():
    rows = [{"y": y, "x": 0, "kind": "~" * 24, "height": "0" * 24, "owner": "." * 24, "slope": "0" * 24,
             "build": "0" * 24, "dock": "0" * 24, "step": 8} for y in range(0, 32, 8)]
    text = render_map(rows)
    first = text.splitlines()[0]
    assert "80" in first and "160" in first  # columns 10 and 20 are x = 80 and 160
    assert "every 8 tiles" in text


def test_tile_view_shows_dock_fit_rail_pieces_and_signals():
    info = {"tile": 5 * 64 + 3, "x": 3, "y": 5, "kind": "#", "height": 2, "max_height": 2, "slope": 0,
            "buildable": False, "terrain": "normal", "owner": 0, "mine": True, "town": -1, "closest_town": None,
            "radius": 3, "accepts": [], "produces": [], "industries": [], "rail": True, "incline": None,
            "dock": {"ok": False, "water_tile": -1, "why": "not an inclined slope"},
            "tracks": ["ne_sw", "nw_ne"], "signals": [{"front_tile": 5 * 64 + 4, "type": "pbs_oneway"}]}
    text = format_tile(info)
    assert "Rail pieces: ne_sw, nw_ne" in text and "pbs_oneway facing 4,5" in text
    assert "Dock: no (not an inclined slope)" in text
