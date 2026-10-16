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
