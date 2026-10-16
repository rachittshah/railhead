"""Seeded scenario sampling: deterministic, split-aware (OOD = unseen climates), within engine ranges."""

from railhead.gen.scenario import sample_scenario, task_id


def test_same_seed_same_scenario():
    assert sample_scenario(17, "train", "deliver") == sample_scenario(17, "train", "deliver")


def test_different_seeds_vary():
    xs = {sample_scenario(s, "train", "deliver")["generation_seed"] for s in range(20)}
    assert len(xs) == 20


def test_train_and_dev_are_temperate_and_ood_is_not():
    for s in range(30):
        assert sample_scenario(s, "train", "deliver")["landscape"] == "temperate"
        assert sample_scenario(s, "dev", "deliver")["landscape"] == "temperate"
        assert sample_scenario(s, "ood", "deliver")["landscape"] in ("arctic", "tropic")


def test_settings_are_in_engine_ranges():
    for s in range(50):
        sc = sample_scenario(s, "train", "tycoon")
        assert 6 <= sc["map_log2"] <= 8
        assert 1930 <= sc["starting_year"] <= 2000
        assert sc["settings"]["difficulty"]["industry_density"] in ("3", "4", "5")


def test_task_ids_are_stable_and_split_tagged():
    assert task_id("deliver", "dev", 7) == "deliver-dev-0007"
