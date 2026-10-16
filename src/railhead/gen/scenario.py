"""Seeded scenario sampling. A task is fully determined by (generator version, family, split, seed).

Splits: ``train`` and ``dev`` are temperate (disjoint seed ranges); ``ood`` uses arctic/tropic,
whose cargo chains (e.g. food/water/rubber) never appear in training.
"""

from __future__ import annotations

import hashlib
import random

GEN_VERSION = 1
SPLIT_SALT = {"train": 0, "dev": 1_000_003, "ood": 2_000_029}


def task_id(family: str, split: str, seed: int) -> str:
    return f"{family}-{split}-{seed:04d}"


def _rng(seed: int, split: str, family: str) -> random.Random:
    h = hashlib.sha256(f"railhead:{GEN_VERSION}:{family}:{split}:{seed + SPLIT_SALT[split]}".encode()).digest()
    return random.Random(int.from_bytes(h[:8], "little"))


def sample_scenario(seed: int, split: str, family: str) -> dict:
    r = _rng(seed, split, family)
    landscape = "temperate" if split in ("train", "dev") else r.choice(["arctic", "tropic"])
    map_log2 = r.choices([6, 7, 8], weights=[1, 6, 2])[0]
    return {
        "gen_version": GEN_VERSION,
        "family": family,
        "split": split,
        "seed": seed,
        "generation_seed": r.randrange(1, 2**31 - 1),
        "landscape": landscape,
        "map_log2": map_log2,
        "starting_year": r.choice([1950, 1960, 1975, 1990, 2000]),
        "settings": {
            "difficulty": {
                "number_towns": str(r.choice([1, 2, 3])),
                "industry_density": str(r.choice([3, 4, 5])),
                "terrain_type": str(r.choice([0, 1, 2])),
                "quantity_sea_lakes": str(r.choice([0, 1, 2])),
                "max_loan": str(r.choice([200_000, 300_000, 500_000])),
                "initial_interest": str(r.choice([2, 3, 4])),
                "vehicle_breakdowns": str(r.choice([0, 1])),
                "construction_cost": "0",
                "vehicle_costs": "0",
                "subsidy_multiplier": "2",
                "disasters": "false",
                "economy": "false",
            },
            "economy": {"type": "1"},  # smooth economy: production drifts slowly (sound bounds)
            "game_creation": {"town_name": "english"},
        },
    }


def server_config(sc: dict):
    """ServerConfig for a sampled scenario."""
    from railhead.server import ServerConfig

    return ServerConfig(
        seed=sc["generation_seed"], map_x_log2=sc["map_log2"], map_y_log2=sc["map_log2"],
        landscape=sc["landscape"], starting_year=sc["starting_year"],
        settings={k: dict(v) for k, v in sc["settings"].items()},
    )
