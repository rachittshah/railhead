"""recheck shards: every task lands in exactly one shard, in a stable order, so CI can split the catalog."""

from pathlib import Path

from railhead.admin_cli import parse_shard, shard_dirs


def test_shards_partition_the_catalog_exactly_once():
    dirs = [Path(f"t{i:03d}") for i in range(10)]
    parts = [shard_dirs(dirs, i, 4) for i in range(4)]
    assert sorted(p for part in parts for p in part) == dirs
    assert parts[0] == [Path("t000"), Path("t004"), Path("t008")]
    assert shard_dirs(dirs, 0, 1) == dirs


def test_recheck_one_removes_its_work_dir(tmp_path, monkeypatch):
    import json

    import railhead.verify
    from railhead.admin_cli import _recheck_one

    (tmp_path / "certificates.json").write_text(json.dumps({"oracle_reward": 0.5}))
    seen = []

    def fake_verify(tdir, start, log, work):
        (work / "replay").mkdir()
        seen.append(work)
        return {"valid": 1, "reward": 0.5}

    monkeypatch.setattr(railhead.verify, "verify", fake_verify)
    assert _recheck_one(str(tmp_path))["match"]
    assert seen and not seen[0].exists()


def test_parse_shard():
    assert parse_shard("2/4") == (2, 4)
    for bad in ("4/4", "-1/4", "1/0", "x"):
        try:
            parse_shard(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
