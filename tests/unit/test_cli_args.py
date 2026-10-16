"""`rh` argument parsing: key=value pairs become typed JSON args; x,y tiles become indices."""

import pytest

from railhead.cli import parse_kv


def test_parse_kv_types():
    assert parse_kv(["n=3", "flag=true", "name=Big Coal", "xs=[1,2]"], map_x=128) == {
        "n": 3, "flag": True, "name": "Big Coal", "xs": [1, 2]}


def test_tile_coordinates_become_indices_for_tile_params():
    assert parse_kv(["tile=10,4", "from_tile=0,1", "depot_tile=127,127"], map_x=128) == {
        "tile": 4 * 128 + 10, "from_tile": 128, "depot_tile": 127 * 128 + 127}


def test_non_tile_params_keep_commas():
    assert parse_kv(["reason=too far, no money"], map_x=128) == {"reason": "too far, no money"}


def test_malformed_pair_is_an_error():
    with pytest.raises(ValueError):
        parse_kv(["oops"], map_x=128)


def test_help_lists_every_registry_verb_with_its_arguments():
    from railhead.actions import REGISTRY
    from railhead.cli import help_text

    text = help_text()
    for name in REGISTRY:
        group, verb = name.split(".")
        assert f"rh {group} {verb}" in text, name
    assert "rh company loan amount=" in text
    assert "rh vehicle attach vehicle=" in text and "train_vehicle=" in text
    assert "rh submit confirm=yes" in text


def test_group_help_lists_only_that_group():
    from railhead.cli import help_text

    text = help_text("vehicle")
    assert "rh vehicle buy" in text and "rh road line" not in text
    assert "depot_tile=X,Y" in text  # tiles are shown as X,Y


def test_unknown_group_help_is_an_error():
    import pytest

    from railhead.cli import help_text

    with pytest.raises(ValueError):
        help_text("nosuchgroup")


def test_group_verb_wins_over_a_query_of_the_same_name():
    """`company` is both a query and an action group: `rh company loan` must be the action."""
    from railhead.cli import route

    assert route("company", ["loan", "amount=300000"]) == ("act", "company.loan")
    assert route("company", ["rename", "name=Acme"]) == ("act", "company.rename")
    assert route("company", []) == ("query", "company")
    assert route("company", ["--json"]) == ("query", "company")
    assert route("vehicles", []) == ("query", "vehicles")
    assert route("road", ["line", "from_tile=1,2"]) == ("act", "road.line")


def test_help_says_how_to_read_a_fork():
    # Opus 5.5 in Arena 2: "Forks can't be inspected" - the reader existed but help did not name it
    from railhead.cli import help_text

    assert "fork q name=F m=company" in help_text()
