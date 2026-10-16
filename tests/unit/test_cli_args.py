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
