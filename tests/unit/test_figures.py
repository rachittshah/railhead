"""Document charts: made from the committed arena data, one SVG per chart and theme."""

import json

import pytest

pytest.importorskip("matplotlib")

from railhead.figures import build, money


def test_money_labels():
    assert [money(v) for v in (45_758_664, 2_574_034, 335_998, 999, -50_557)] == \
        ["£45.8M", "£2.57M", "£336k", "£999", "−£50.6k"]


def test_build_writes_each_chart_in_both_themes_with_direct_labels(tmp_path):
    doc = {"task": "arena-x",
           "results": [{"player": "claude-a", "status": "verified", "net_worth": 9_000_000, "vehicles": 3},
                       {"player": "gpt-b", "status": "verified", "net_worth": 2_000_000, "vehicles": 5},
                       {"player": "baseline", "status": "certificate", "net_worth": 150_000, "gain_x_baseline": 1.0},
                       {"player": "gpt-c", "status": "playing", "net_worth_live": 10, "reached": "1961-01-01"}],
           "curves": {"claude-a": [{"date": "1960-06-01", "net_worth": 0}, {"date": "1961-06-01", "net_worth": 9e6}],
                      "gpt-b": [{"date": "1960-06-01", "net_worth": 0}, {"date": "1961-06-01", "net_worth": 2e6}]},
           "fleet_mix": {"claude-a": {"road": 0, "rail": 0, "air": 3, "water": 0},
                         "gpt-b": {"road": 4, "rail": 1, "air": 0, "water": 0}}}
    src = tmp_path / "arena-x.json"
    src.write_text(json.dumps(doc))
    out = build([src], tmp_path / "img")
    names = sorted(p.name for p in out)
    assert names == sorted(f"arena-x-{c}{t}.svg" for c in ("networth", "growth", "fleet") for t in ("", "-dark"))
    svg = (tmp_path / "img/arena-x-networth.svg").read_text()
    assert "£9.00M" in svg and "claude-a" in svg and "still playing" in svg  # text stays text, labels direct
    assert "#2a78d6" in svg and "#3987e5" in (tmp_path / "img/arena-x-networth-dark.svg").read_text()


def test_free_models_get_their_own_colour_and_legend_entry(tmp_path):
    from railhead.figures import vendor

    assert [vendor(p) for p in ("claude-opus-5-5", "gpt-6-astra", "beam-free")] == ["claude", "openai", "free"]
    doc = {"task": "arena-y",
           "results": [{"player": "beam-free", "status": "verified", "net_worth": 400_000},
                       {"player": "gpt-6-astra", "status": "verified", "net_worth": 900_000}],
           "curves": {}, "fleet_mix": {"beam-free": {"road": 3, "rail": 0, "air": 0, "water": 0},
                                       "gpt-6-astra": {"road": 0, "rail": 0, "air": 4, "water": 0}}}
    src = tmp_path / "arena-y.json"
    src.write_text(json.dumps(doc))
    build([src], tmp_path / "img")
    svg = (tmp_path / "img/arena-y-networth.svg").read_text()
    assert "#1baf7a" in svg and "OpenCode Zen free models" in svg and "Claude (Claude Code)" not in svg


def test_networth_subtitle_counts_divergences_from_the_data(tmp_path):
    """The subtitle used to say "0 divergences" whatever the data held."""
    rows = [{"player": "claude-a", "status": "verified", "net_worth": 9_000_000, "divergences": 2},
            {"player": "baseline", "status": "certificate", "net_worth": 150_000, "gain_x_baseline": 1.0}]
    src = tmp_path / "arena-d.json"
    curves = {"claude-a": [{"date": "1960-06-01", "net_worth": 0}, {"date": "1961-06-01", "net_worth": 9e6}]}
    mix = {"claude-a": {"road": 1, "rail": 0, "air": 0, "water": 0}}
    src.write_text(json.dumps({"task": "arena-d", "results": rows, "curves": curves, "fleet_mix": mix}))
    build([src], tmp_path / "img")
    svg = (tmp_path / "img/arena-d-networth.svg").read_text()
    assert "2 divergences" in svg and "0 divergences" not in svg


def test_figures_cli_without_data_prints_usage(capsys):
    from railhead.figures import main

    assert main([]) == 2 and "usage: railhead figures" in capsys.readouterr().err
