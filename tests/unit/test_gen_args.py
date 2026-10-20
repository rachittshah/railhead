"""`railhead gen` argument parsing: --years and --map reach the arena family only (no engine)."""

from pathlib import Path

import pytest

from railhead.gen import build
from railhead.gen.build import parse_gen_args


def test_defaults_give_no_family_options():
    g = parse_gen_args(["arena", "train", "0", "8"])
    assert (g.family, g.split, g.lo, g.hi, g.out, g.jobs, g.options) == ("arena", "train", 0, 8, Path("tasks"), 4, {})


def test_arena_years_and_map_become_generator_options():
    g = parse_gen_args(["arena", "dev", "212", "223", "--years", "10", "--map", "512", "--jobs", "2", "--out", "x"])
    assert (g.lo, g.hi, g.jobs, g.out) == (212, 223, 2, Path("x"))
    assert g.options == {"years": 10, "map_log2": 9}


@pytest.mark.parametrize("size,log2", [("256", 8), ("512", 9), ("1024", 10)])
def test_map_sizes_convert_to_log2(size, log2):
    assert parse_gen_args(["arena", "train", "0", "1", "--map", size]).options == {"map_log2": log2}


@pytest.mark.parametrize("bad", [["--map", "300"], ["--map", "128"], ["--years", "0"], ["--years", "ten"]])
def test_bad_values_are_rejected(bad):
    with pytest.raises(ValueError):
        parse_gen_args(["arena", "train", "0", "1", *bad])


@pytest.mark.parametrize("family", ["deliver", "deliver_bid", "impossible", "simops", "tycoon"])
@pytest.mark.parametrize("opt", [["--years", "10"], ["--map", "512"]])
def test_other_families_reject_arena_options(family, opt):
    with pytest.raises(ValueError, match="arena"):
        parse_gen_args([family, "train", "0", "1", *opt])


def test_main_prints_the_error_and_starts_no_engine(capsys):
    assert build.main(["tycoon", "train", "0", "1", "--years", "10"]) == 2
    assert "only for the arena family" in capsys.readouterr().err


def test_missing_positional_arguments_are_rejected():
    with pytest.raises(ValueError, match="usage"):
        parse_gen_args(["arena", "train", "0"])


def test_gen_arena_scales_horizon_fork_days_and_map(monkeypatch):
    seen = {}
    monkeypatch.setattr(build, "gen_tycoon", lambda *a, **k: seen.update(k) or "spec")
    build.gen_arena(1, "train", Path("t"), years=10, map_log2=9)
    assert seen == {"family": "arena", "horizon": 3650, "runoff": 365, "map_log2": 9, "max_fork_days": 730}
    build.gen_arena(1, "train", Path("t"))
    assert seen == {"family": "arena", "horizon": 1825, "runoff": 365, "map_log2": 8, "max_fork_days": 365}
