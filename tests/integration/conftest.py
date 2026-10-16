"""Shared fixtures: a live World (patched engine, GS bridge, bound company, unlimited speed)."""

import pytest

from railhead.server import ServerConfig
from railhead.world import World


def make_config(seed: int = 3, size_log2: int = 7, **settings) -> ServerConfig:
    """128x128 temperate map with a few towns and normal industry density by default."""
    base = {
        "difficulty": {"number_towns": "2", "industry_density": "4", "max_loan": "300000"},
        "game_creation": {"town_name": "english"},
    }
    for section, kv in settings.items():
        base.setdefault(section, {}).update(kv)
    return ServerConfig(seed=seed, map_x_log2=size_log2, map_y_log2=size_log2, settings=base)


@pytest.fixture
def world(tmp_path):
    with World.create(tmp_path / "world", make_config()) as w:
        yield w
