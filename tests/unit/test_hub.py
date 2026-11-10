"""Dataset card: valid HF front matter with one data file per split and per config."""

from railhead.hub import write_card


def test_card_lists_every_split_for_both_configs(tmp_path):
    counts = {"tasks": {"train": 10, "dev": 3, "ood": 2}, "trajectories": {"train": 10, "dev": 3}}
    text = write_card(tmp_path, counts, image="ghcr.io/x/railhead:0.1.0").read_text()
    front = text.split("---")[1]
    for s in ("train", "dev", "ood"):
        assert f"path: tasks/{s}.parquet" in front
    assert "path: trajectories/dev.parquet" in front and "trajectories/ood" not in front
    assert "| tasks | train | 10 |" in text
    assert "ghcr.io/x/railhead:0.1.0" in text


def test_merge_games_replaces_the_same_task_and_policy_and_keeps_the_rest():
    from railhead.hub import merge_games

    old = [{"task_id": "t", "policy": "oracle", "reward": 0.5}, {"task_id": "t", "policy": "opus", "reward": 0.1},
           {"task_id": "u", "policy": "opus", "reward": 0.2}]
    new = [{"task_id": "t", "policy": "opus", "reward": 1.0}, {"task_id": "t", "policy": "fable", "reward": 0.9}]
    out = merge_games(old, new)
    assert {(r["task_id"], r["policy"]): r["reward"] for r in out} == {
        ("t", "oracle"): 0.5, ("t", "opus"): 1.0, ("u", "opus"): 0.2, ("t", "fable"): 0.9}
    assert merge_games(out, new) == out  # idempotent


def test_merge_games_drops_rows_under_an_alias_of_a_renamed_player():
    from railhead.hub import merge_games

    old = [{"task_id": "t", "policy": "opus", "reward": 0.1}, {"task_id": "t", "policy": "claude-opus-5-5", "reward": 0.2}]
    new = [{"task_id": "t", "policy": "claude-opus-5-5", "reward": 0.5}]
    assert merge_games(old, new, aliases={"claude-opus-5-5": "opus"}) == new


def _task(tasks, tid):
    from railhead.task import TaskSpec

    d = tasks / tid
    d.mkdir(parents=True)
    TaskSpec(task_id=tid, family="deliver", horizon_days=300, split=tid.split("-")[1], briefing="b").save(d)
    (d / "start.sav").write_bytes(b"sav")


def test_task_rows_find_the_harbor_task_inside_a_dataset_directory(tmp_path):
    # USAGE 12.1 exports into harbor/railhead (dev + ood) and harbor/railhead-train (train)
    from railhead.hub import task_rows

    _task(tmp_path / "tasks", "deliver-dev-aaaaaa")
    _task(tmp_path / "tasks", "deliver-train-bbbbbb")
    for ds, tid in (("railhead", "deliver-dev-aaaaaa"), ("railhead-train", "deliver-train-bbbbbb")):
        (tmp_path / "harbor" / ds / tid).mkdir(parents=True)
        (tmp_path / "harbor" / ds / tid / "instruction.md").write_text(f"# {tid}")
    rows = {r["task_id"]: r for r in task_rows(tmp_path / "tasks", tmp_path / "harbor")}
    assert rows["deliver-dev-aaaaaa"]["instruction"] == "# deliver-dev-aaaaaa"
    assert rows["deliver-train-bbbbbb"]["task_binary"]


def test_task_rows_refuse_a_harbor_directory_without_the_task(tmp_path):
    import pytest

    from railhead.hub import task_rows

    _task(tmp_path / "tasks", "deliver-dev-aaaaaa")
    (tmp_path / "harbor").mkdir()
    with pytest.raises(FileNotFoundError, match="deliver-dev-aaaaaa"):
        task_rows(tmp_path / "tasks", tmp_path / "harbor")
