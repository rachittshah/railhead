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
