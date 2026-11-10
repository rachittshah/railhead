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


def test_arena_briefing_states_the_log50_scale():
    """Arena tasks score with rewards.tycoon_log (baseline ~0.18), not the linear 0.5/1 tycoon scale."""
    import json
    import math

    text = build.tycoon_briefing("arena", 1825, 365, 185775)
    assert "scores about 0.18" in text and "50 times" in text and "scores 0.5" not in text
    assert round(math.log1p(1) / math.log1p(50), 2) == 0.18
    shipped = json.loads((Path(__file__).parents[2] / "tasks/arena-dev-0005/task.json").read_text())["briefing"]
    assert text == shipped


def test_tycoon_briefing_keeps_the_linear_scale():
    text = build.tycoon_briefing("tycoon", 1825, 365, 1000)
    assert "scores 0.5" in text and "doubling its gain scores 1" in text


def test_unknown_family_is_rejected_before_any_worker_starts():
    with pytest.raises(ValueError, match="unknown family"):
        parse_gen_args(["delivr", "train", "0", "1"])
    assert parse_gen_args(["simops", "dev", "0", "1"]).family == "simops"


def test_gen_exits_nonzero_when_a_generator_crashes(tmp_path, monkeypatch):
    """`railhead gen` used to print `error:` per seed and still exit 0."""

    class _Inline:  # run the worker in-process so the monkeypatched generator is used
        def __init__(self, max_workers):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, fn, *args):
            from concurrent.futures import Future

            f = Future()
            f.set_result(fn(*args))
            return f

    def boom(seed, split, out, **kw):
        raise RuntimeError("engine missing")

    monkeypatch.setattr(build, "ProcessPoolExecutor", _Inline)
    monkeypatch.setitem(build.GENERATORS, "tycoon", boom)
    assert build.main(["tycoon", "dev", "0", "2", "--out", str(tmp_path)]) == 1
