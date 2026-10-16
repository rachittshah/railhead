"""recheck shards: every task lands in exactly one shard, in a stable order, so CI can split the catalog."""

from pathlib import Path

from railhead.admin_cli import parse_shard, shard_dirs


def test_shards_partition_the_catalog_exactly_once():
    dirs = [Path(f"t{i:03d}") for i in range(10)]
    parts = [shard_dirs(dirs, i, 4) for i in range(4)]
    assert sorted(p for part in parts for p in part) == dirs
    assert parts[0] == [Path("t000"), Path("t004"), Path("t008")]
    assert shard_dirs(dirs, 0, 1) == dirs


def test_parse_shard():
    assert parse_shard("2/4") == (2, 4)
    for bad in ("4/4", "-1/4", "1/0", "x"):
        try:
            parse_shard(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
