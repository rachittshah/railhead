"""Script-AI anchors: row shaping, sampling, fairness checks and the play loop (fake world, no engine)."""

from __future__ import annotations

import io
import json
import math
import tarfile
from pathlib import Path

import pytest

from railhead import anchors
from railhead.bridge import BridgeError
from railhead.task import TaskSpec


# ------------------------------------------------------------------ a fake world
class FakeAdmin:
    def __init__(self, world: FakeWorld) -> None:
        self.world = world
        self.sent: list[str] = []

    def rcon(self, cmd: str) -> list[str]:
        self.sent.append(cmd)
        w = self.world
        if cmd.startswith("start_ai"):
            if w.ai_fails:
                return ["Failed to load the specified AI"]
            w.companies[1] = {"money": 300_000, "loan": 300_000, "max_loan": 500_000, "value": 0, "veh": []}
            return []
        if cmd.startswith("reseed"):
            return [f"Reseeded: {cmd.split()[1]}"]
        if cmd.startswith("setting "):
            return [f"Current value for '{cmd.split()[1]}' is '7'"]
        return []


class FakeWorld:
    """Day counter world. The AI company (id 1) earns ``rate`` per day and buys a bus every 100 days."""

    def __init__(self, start: int = 715_000, rate: int = 1_000, dies_at: int | None = None,
                 ai_fails: bool = False) -> None:
        self.date = start
        self.start = start
        self.bound = 0
        self.rate = rate
        self.dies_at = dies_at
        self.ai_fails = ai_fails
        self.companies = {0: {"money": 463_700, "loan": 500_000, "max_loan": 500_000, "value": 0, "veh": []}}
        self.admin = FakeAdmin(self)
        self.steps: list[int] = []

    def ymd(self) -> str:
        return f"d{self.date - self.start}"

    def state(self) -> dict:
        return {"date": self.date, "ymd": self.ymd(), "company": self.bound, "ticks": 0}

    def step(self, *, days: int, timeout: float = 0) -> dict:
        self.steps.append(days)
        for _ in range(days):
            self.date += 1
            if 1 in self.companies:
                c = self.companies[1]
                c["money"] += self.rate
                if (self.date - self.start) % 100 == 0:
                    c["veh"].append({"vtype": "road"})
            if self.dies_at is not None and self.date - self.start >= self.dies_at:
                self.companies.pop(1, None)
        return self.state()

    def call(self, method: str, timeout: float = 60.0, **a):
        if method == "companies":
            return [{"id": i, "money": c["money"], "loan": c["loan"], "value": c["value"]}
                    for i, c in self.companies.items()]
        if method == "bind_company":
            if a["company"] not in self.companies:
                raise BridgeError("ERR_NO_COMPANY")
            self.bound = a["company"]
            return {"company": self.bound}
        c = self.companies.get(self.bound)
        if c is None:
            raise BridgeError("ERR_NO_COMPANY")
        if method == "company":
            return {"id": self.bound, "money": c["money"], "loan": c["loan"], "max_loan": c["max_loan"],
                    "loan_interval": 10_000, "value": c["value"]}
        if method == "vehicles":
            return list(c["veh"])
        if method == "stations":
            return [{"id": 1}, {"id": 2}] if c["veh"] else []
        raise AssertionError(method)


def spec(horizon: int = 365, runoff: int = 100) -> TaskSpec:
    return TaskSpec(task_id="t-1", family="tycoon", horizon_days=horizon, runoff_days=runoff,
                    objective={"w_idle": -36_300, "w_base": 147_407, "score": "log50"})


# ------------------------------------------------------------------ content-state parsing and provenance
def test_parse_content_state_reads_id_type_state_name():
    lines = ["id, type, state, name",
             "10884238, AI, Not selected, AdmiralAI, 4C4D4441, 4CCD92FB8F8F01045145BE99A28E14A6",
             "11431842, AI, Installed, NoCAB - Bleeding Edge edition, 42414342, 7B5DD7884EF6C2A9234201B9CFA26FAE",
             "garbage"]
    rows = anchors.parse_content_state(lines)
    assert rows == [
        {"id": 10884238, "type": "AI", "state": "Not selected", "name": "AdmiralAI", "unique_id": "4C4D4441",
         "md5": "4CCD92FB8F8F01045145BE99A28E14A6"},
        {"id": 11431842, "type": "AI", "state": "Installed", "name": "NoCAB - Bleeding Edge edition",
         "unique_id": "42414342", "md5": "7B5DD7884EF6C2A9234201B9CFA26FAE"},
    ]


def test_pick_content_prefers_exact_ai_name_and_rejects_ambiguity():
    rows = [{"id": 1, "type": "AI", "name": "NoCAB"}, {"id": 2, "type": "AI", "name": "NoCAB - Bleeding Edge edition"},
            {"id": 3, "type": "AI", "name": "Mungo"}, {"id": 4, "type": "AI", "name": "Mungo"},
            {"id": 5, "type": "AI library", "name": "SuperLib"}]
    assert anchors.pick_content(rows, "nocab")["id"] == 1
    assert anchors.pick_content(rows, "4")["id"] == 4
    with pytest.raises(ValueError, match="ambiguous"):
        anchors.pick_content(rows, "Mungo")
    with pytest.raises(ValueError, match="not on the content service"):
        anchors.pick_content(rows, "SuperLib")


def _tar(path: Path, files: dict[str, str]) -> Path:
    with tarfile.open(path, "w") as t:
        for name, text in files.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return path


def test_tar_provenance_reads_name_version_and_licence(tmp_path):
    p = _tar(tmp_path / "Foo-7.tar", {
        "Foo-7/info.nut": 'class FooInfo extends AIInfo {\n function GetName() { return "Foo"; }\n'
                          ' function GetVersion()  { return 7; }\n}',
        "Foo-7/LICENSE.txt": "\n  GNU GENERAL PUBLIC LICENSE\n Version 2",
    })
    assert anchors.tar_provenance(p) == {"file": "Foo-7.tar", "name": "Foo", "version": 7, "library": False,
                                         "licence": "GNU GENERAL PUBLIC LICENSE"}


def test_tar_provenance_marks_libraries_and_missing_licence(tmp_path):
    p = _tar(tmp_path / "Lib.tar", {"Lib/library.nut": 'class L extends AILibrary {\n function GetName() '
                                    '{ return "Lib"; }\n function GetVersion() { return 3; } }'})
    prov = anchors.tar_provenance(p)
    assert prov["library"] is True and prov["version"] == 3 and prov["licence"] is None


def test_write_manifest_joins_service_rows_by_byte_reversed_unique_id(tmp_path):
    d = tmp_path / "content" / "ai"
    d.mkdir(parents=True)
    _tar(d / "41444d4c-AdmiralAI-25.tar", {"a/info.nut": 'function GetName() { return "X"; }\n'
                                          'function GetVersion() { return 25; }'})
    listing = [{"id": 10884238, "type": "AI", "state": "Installed", "name": "AdmiralAI", "unique_id": "4C4D4441",
                "md5": "AB"}]
    (row,) = anchors.write_manifest(tmp_path, listing)
    assert row["name"] == "AdmiralAI" and row["content_id"] == 10884238 and row["package_version"] == "25"
    assert row["script_name"] == "X"
    assert anchors.ai_info(tmp_path, "x")["content_id"] == 10884238
    assert anchors.ai_info(tmp_path, "AdmiralAI")["content_id"] == 10884238
    assert json.loads((tmp_path / "manifest.json").read_text())[0]["unique_id"] == "4C4D4441"


# ------------------------------------------------------------------ sampling and fairness
def test_sample_counts_vehicles_by_mode_and_net_worth():
    w = FakeWorld()
    w.companies[1] = {"money": 1_000, "loan": 400, "max_loan": 0, "value": 55,
                      "veh": [{"vtype": "road"}, {"vtype": "rail"}, {"vtype": "road"}, {"vtype": "air"}]}
    s = anchors.sample(w, ai_id=1, model_id=0, start_date=w.start)
    assert s == {"date": "d0", "day": 0, "money": 1_000, "loan": 400, "net_worth": 600, "value": 55,
                 "vehicles": 4, "fleet": {"road": 2, "rail": 1, "air": 1, "water": 0}, "stations": 2,
                 "model_net_worth": -36_300}


def test_sample_returns_none_when_the_company_is_gone():
    w = FakeWorld()
    assert anchors.sample(w, ai_id=1, model_id=0, start_date=w.start) is None


def test_parity_reports_every_difference():
    model = {"date": "1960-01-01", "money": 463_700, "loan": 500_000, "max_loan": 500_000, "value": 0}
    ai = {"date": "1960-01-01", "money": 300_000, "loan": 300_000, "max_loan": 500_000, "value": 0}
    p = anchors.parity(model, ai)
    assert p["same"] is False
    assert p["diff"] == {"money": -163_700, "loan": -200_000}
    assert p["net_worth"] == {"model": -36_300, "ai": 0}
    assert anchors.parity(model, dict(model))["same"] is True


def test_crash_detection_counts_new_script_deaths_only():
    before = "x\nThe script died unexpectedly.\n"
    assert anchors.script_deaths(before) == 1
    assert anchors.script_deaths(before + "Your script made an error: boom\nThe script died unexpectedly.\n") == 2
    assert anchors.last_script_error("a\nYour script made an error: the index 'x' does not exist\nb") == \
        "the index 'x' does not exist"


# ------------------------------------------------------------------ play loop
def test_play_runs_mandate_then_runoff_and_samples_on_chunks():
    w = FakeWorld()
    res = anchors.play(w, spec(365, 100), "Foo", chunk_days=91)
    assert sum(w.steps) == 465
    assert max(w.steps) <= 91
    assert res["status"] == "ok"
    assert res["ai_company"] == 1 and res["model_company"] == 0
    assert res["mandate"]["day"] == 365 and res["final"]["day"] == 465
    assert res["mandate"]["net_worth"] == 365 * 1_000
    assert res["final"]["net_worth"] == 465 * 1_000
    assert res["final"]["fleet"]["road"] == 4
    assert res["samples"][0]["day"] == 0
    assert res["parity"]["diff"] == {"money": -163_700, "loan": -200_000}
    assert 'start_ai "Foo"' in w.admin.sent


def test_play_reseeds_after_the_company_exists():
    w = FakeWorld()
    anchors.play(w, spec(10, 0), "Foo", seed=2)
    sent = w.admin.sent
    assert sent.index('start_ai "Foo"') < sent.index(f"reseed {anchors.reseed_value(2)}")
    w0 = FakeWorld()
    anchors.play(w0, spec(10, 0), "Foo", seed=0)
    assert not any(c.startswith("reseed") for c in w0.admin.sent)


def test_play_passes_ai_settings():
    w = FakeWorld()
    anchors.play(w, spec(10, 0), "Foo", ai_settings="use_trains=1")
    assert 'start_ai "Foo" use_trains=1' in w.admin.sent


def test_play_records_bankruptcy_as_a_result():
    w = FakeWorld(dies_at=200)
    res = anchors.play(w, spec(365, 100), "Foo", chunk_days=50)
    assert res["status"] == "bankrupt"
    assert res["gone_date"] == "d200"
    assert res["mandate"] is None and res["final"] is None
    assert res["samples"][-1]["day"] == 150


def test_play_records_ai_that_does_not_start():
    w = FakeWorld(ai_fails=True)
    res = anchors.play(w, spec(), "Nope", start_timeout=0.05)
    assert res["status"] == "failed_to_start"
    assert "Failed to load" in res["rcon"][0]


def test_play_records_crash_and_keeps_running():
    w = FakeWorld()
    logs = iter(["", "", "Your script made an error: bad\nThe script died unexpectedly.\n"] + [
        "Your script made an error: bad\nThe script died unexpectedly.\n"] * 20)
    res = anchors.play(w, spec(365, 0), "Foo", chunk_days=100, log_text=lambda: next(logs))
    assert res["status"] == "crashed"
    assert res["crash"] == {"date": "d200", "error": "bad"}
    assert res["mandate"]["day"] == 365


# ------------------------------------------------------------------ rows and documents
def test_row_matches_arena_row_shape_and_rounding():
    res = {"status": "ok", "ai": "Foo", "seed": 1, "reseed": anchors.reseed_value(1),
           "mandate": {"date": "1970-01-01", "net_worth": 1_000_000, "vehicles": 3, "stations": 2,
                       "fleet": {"road": 3, "rail": 0, "air": 0, "water": 0}, "value": 9},
           "final": {"date": "1971-01-01", "net_worth": 2_000_000, "vehicles": 5, "stations": 4,
                     "fleet": {"road": 4, "rail": 1, "air": 0, "water": 0}, "value": 10},
           "samples": [], "parity": {"same": False}, "crash": None, "gone_date": None, "wall_seconds": 12.5}
    row = anchors.make_row(spec(), res, info={"version": 7, "content_id": 42, "md5": "AB", "licence": "GPL"})
    gain = (2_000_000 + 36_300) / (147_407 + 36_300)
    assert row["player"] == "anchor:Foo"
    assert row["status"] == "anchor"
    assert row["net_worth"] == 2_000_000
    assert row["net_worth_horizon"] == 1_000_000
    assert row["gain_x_baseline"] == round(gain, 1)
    assert row["score_k50"] == round(math.log1p(gain) / math.log1p(50), 3)
    assert row["score_k1000"] == round(math.log1p(gain) / math.log1p(1000), 3)
    assert row["vehicles"] == 5 and row["stations"] == 4 and row["reached"] == "1971-01-01"
    assert row["fleet_horizon"] == {"road": 3, "rail": 0, "air": 0, "water": 0}
    assert row["seed"] == 1 and row["ai_version"] == 7 and row["content_id"] == 42
    assert row["runoff_generous_to_ai"] is True


def test_row_for_bankrupt_ai_has_no_net_worth():
    res = {"status": "bankrupt", "ai": "Foo", "seed": 0, "reseed": None, "mandate": None, "final": None,
           "samples": [{"date": "d3", "net_worth": -5, "vehicles": 0, "stations": 0, "fleet": {}}],
           "parity": {}, "crash": None, "gone_date": "d4", "wall_seconds": 1.0}
    row = anchors.make_row(spec(), res, info={})
    assert row["net_worth"] is None and row["net_worth_horizon"] is None
    assert row["gain_x_baseline"] is None and row["outcome"] == "bankrupt" and row["reached"] == "d4"


def test_merge_doc_replaces_same_player_and_seed_and_grows_to_many_tasks():
    r1 = {"player": "anchor:Foo", "seed": 0, "net_worth": 1}
    doc = anchors.merge_doc({}, "a", [r1], {"anchor:Foo#0": [{"date": "x"}]}, {"anchor:Foo#0": {"road": 1}})
    assert doc["task"] == "a" and doc["results"] == [r1] and doc["curves"]["anchor:Foo#0"] == [{"date": "x"}]
    r1b = dict(r1, net_worth=2)
    doc = anchors.merge_doc(doc, "a", [r1b], {}, {})
    assert doc["results"] == [r1b]
    doc = anchors.merge_doc(doc, "b", [r1], {}, {})
    assert [d["task"] for d in doc["tasks"]] == ["a", "b"]
    doc = anchors.merge_doc(doc, "b", [dict(r1, seed=1)], {}, {})
    assert len(doc["tasks"][1]["results"]) == 2
    json.dumps(doc)


def test_add_to_doc_writes_and_merges_a_file(tmp_path):
    out = tmp_path / "d" / "a.json"
    anchors.add_to_doc(out, "a", [{"player": "anchor:Foo", "seed": 0}], {}, {})
    anchors.add_to_doc(out, "a", [{"player": "anchor:Foo", "seed": 1}], {}, {})
    assert len(json.loads(out.read_text())["results"]) == 2


def test_curve_points_use_arena_curve_keys():
    res = {"samples": [{"date": "d0", "net_worth": -1, "money": 2, "loan": 3, "vehicles": 0, "stations": 0,
                        "value": 0, "fleet": {}, "day": 0, "model_net_worth": 0}]}
    assert anchors.curve(res) == [{"date": "d0", "net_worth": -1, "money": 2, "loan": 3, "vehicles": 0,
                                   "stations": 0, "value": 0}]


def test_parse_args():
    a = anchors.parse_args(["tasks/x", "--ai", "Foo", "--ai", "Bar", "--seeds", "3", "--out", "o.json",
                            "--chunk-days", "30", "--ai-settings", "k=1"])
    assert a.task_dir == Path("tasks/x") and a.ai == ["Foo", "Bar"] and a.seeds == 3
    assert a.out == Path("o.json") and a.chunk_days == 30 and a.ai_settings == "k=1"
    assert a.first_seed == 0 and anchors.parse_args(["t", "--ai", "X", "--first-seed", "1"]).first_seed == 1
    f = anchors.parse_args(["fetch", "AdmiralAI", "AIAI"])
    assert f.cmd == "fetch" and f.names == ["AdmiralAI", "AIAI"]
