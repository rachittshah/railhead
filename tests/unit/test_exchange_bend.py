"""The Bend differential oracle: reference output contract, record extraction, corpus determinism and
coverage, the binary plumbing (with fake binaries), and the differential run itself when a Bend binary
exists (``RAILHEAD_BEND_EX`` or bend/build/waybill-ex)."""

import hashlib
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

import pytest

from railhead.admin_cli import main as admin_main
from railhead.exchange import Exchange, corpus
from railhead.exchange.bend import (
    BendError,
    canonical,
    compare,
    describe_diff,
    extract_ex_records,
    find_binary,
    normalize,
    outcome_label,
    reference,
    run_bend,
)
from railhead.exchange.exchange import MAX_MESSAGE_CHARS

SRC = Path(__file__).resolve().parents[2] / "src" / "railhead" / "exchange"
# Codes `apply` cannot raise: tender generation (bad_snapshot), the unused ledger-level settle_supply
# (escrow_mismatch; its bad_arm duplicates init's), lifecycle moves always guarded by an earlier check
# (bad_transition), and transfers whose accounts were all checked before (unknown_account).
UNREACHABLE = {"bad_snapshot", "escrow_mismatch", "bad_transition", "unknown_account"}


def _session_records(arm="enforced", rules=1):
    ex = Exchange(["a", "b"], arm=arm, rules=rules, horizon_tick=40_000 if rules == 2 else None,
                  start_balances={"b": 500})
    ex.offer(0, "a", "supply", {"seller": "a", "buyer": "b", "cargo": 1, "industry": 2, "quantity": 10,
                                "window_start": 0, "deadline": 100, "price": 300, "penalty_per_unit": 4}, to="b")
    ex.accept(1, "b", "C1")
    ex.msg(2, "a", "b", "héllo \U0001f69a")
    ex.settle_due(100, lambda *a: 6)
    return ex.records


# ============================================================================================== reference
def test_normalize_is_python_canonical_jsonl():
    recs = [{"z": 1, "a": "é\U0001f600"}, [1.5, None, True], float("nan")]
    text = normalize(recs)
    assert text == '{"z": 1, "a": "\\u00e9\\ud83d\\ude00"}\n[1.5, null, true]\nNaN\n'
    assert normalize([]) == ""
    assert text.isascii()


def test_reference_ok_is_canonical_state():
    recs = _session_records()
    line = reference(recs)
    assert line == canonical({"ok": Exchange.replay(recs).state()})
    assert line.startswith('{"ok":{"arm":"enforced",') and "\n" not in line and line.isascii()


def test_reference_error_codes_and_positions():
    recs = _session_records()
    assert reference([]) == '{"error":"invariant","seq":0}'  # check_invariants asserts the ledger exists
    assert reference(recs[:1] + [{**recs[1], "seq": 7}]) == '{"error":"bad_record","seq":1}'
    assert reference([{**recs[0], "op": ["init"]}]) == '{"error":"internal","seq":0}'  # unhashable op
    bad_kind = {**recs[1], "kind": ["supply"]}
    assert reference(recs[:1] + [bad_kind]) == '{"error":"internal","seq":1}'      # unhashable kind
    assert reference([None]) == '{"error":"bad_record","seq":0}'
    assert reference(recs[:1] + [recs[2]]) == '{"error":"bad_record","seq":1}'     # index, not the seq field


def test_reference_runs_on_the_wire_form():
    """Two separate surrogates are one character after json round trip: 4001 in memory, 4000 on the wire."""
    ex = Exchange(["a", "b"])
    text = "x" * (MAX_MESSAGE_CHARS - 1) + "\ud83d" + "\ude00"
    assert len(text) == MAX_MESSAGE_CHARS + 1
    rec = {"op": "msg", "seq": 1, "tick": 0, "company": "a", "to": "b", "text": text}
    assert outcome_label(reference([ex.records[0], rec])) == "ok"
    tup = {**ex.records[0], "companies": ("a", "b")}  # a tuple is a list on the wire
    assert outcome_label(reference([tup])) == "ok"


def test_reference_python_equality_sites():
    recs = _session_records()
    msg_at = next(i for i, r in enumerate(recs) if r["op"] == "msg")
    floaty = recs[:msg_at] + [{**recs[msg_at], "seq": float(msg_at)}]
    out = json.loads(reference(floaty))
    assert out["ok"]["messages"][0]["seq"] == float(msg_at)  # echoed as a float token
    assert f'"seq":{msg_at}.0' in reference(floaty)
    assert outcome_label(reference(recs[:msg_at] + [{**recs[msg_at], "seq": msg_at + 0.5}])) == "bad_record"


def test_describe_diff_points_at_first_difference():
    assert describe_diff('{"ok":1}', '{"ok":1}') is None
    assert describe_diff('{"ok":{"a":[1,2]}}', '{"ok":{"a":[1,3]}}') == "$.ok.a[1]: 2 != 3"
    assert describe_diff('{"ok":1}', '{"ok":1.0}').startswith("$.ok:")
    assert describe_diff('{"ok":{"a":1}}', '{"ok":{}}') == "$.ok.a: only in expected"
    assert describe_diff('{"ok":1}', '{"ok": 1}').startswith("byte 6")
    assert describe_diff('{"ok":1}', "garbage").startswith("byte 0")


# ============================================================================================== extraction
def _write_merged(path, records, torn=False):
    lines = [{"kind": "header", "v": 3, "companies": [], "ticks": 0, "checksum": 1}]
    for i, r in enumerate(records):
        lines.append({"kind": "ex", "seq": i + 1, "ticks": r["tick"], "company": r["company"], "x": r})
        lines.append({"kind": "step", "seq": i + 1, "ticks": 0, "end_ticks": 1, "checksum": 1, "wakers": []})
    text = "".join(json.dumps(x, separators=(",", ":")) + "\n" for x in lines)
    path.write_text(text + ('{"kind":"ex","seq":99,"x":{"op"' if torn else ""))


def test_extract_run_dir_merged_log_and_torn_tail(tmp_path):
    recs = _session_records()
    run = tmp_path / "run1" / "coord" / "run"
    run.mkdir(parents=True)
    _write_merged(run / "actions.jsonl", recs, torn=True)
    before = (run / "actions.jsonl").read_bytes()
    assert extract_ex_records(tmp_path / "run1") == recs
    assert extract_ex_records(run / "actions.jsonl") == recs
    assert (run / "actions.jsonl").read_bytes() == before  # the torn line is dropped in memory only
    with pytest.raises(FileNotFoundError):
        extract_ex_records(tmp_path)


def test_extract_plain_records_keeps_non_dicts(tmp_path):
    p = tmp_path / "case.jsonl"
    p.write_text(normalize([{"op": "x", "kind": "header"}, None, [1], "init"]))
    assert extract_ex_records(p) == [{"op": "x", "kind": "header"}, None, [1], "init"]
    p.write_text('{"kind": "ex", "x": {}}\n{"kind": "header"}\n')  # header not first: a plain records file
    assert extract_ex_records(p) == [{"kind": "ex", "x": {}}, {"kind": "header"}]
    p.write_text("")
    assert extract_ex_records(p) == []


# ============================================================================================== corpus
def _digest(cases):
    return hashlib.sha256("".join(normalize(c) + "\x00" for c in cases).encode()).hexdigest()


def test_corpus_is_deterministic_in_process():
    for kind in ("sessions", "mutations", "edge"):
        assert _digest(corpus.generate(kind, 3, 12)) == _digest(corpus.generate(kind, 3, 12))
    assert _digest(corpus.generate("sessions", 3, 5)) != _digest(corpus.generate("sessions", 4, 5))
    assert corpus.generate("sessions", 3, 6)[:3] == corpus.generate("sessions", 3, 3)


def test_corpus_is_deterministic_across_hash_seeds():
    code = ("import hashlib; from railhead.exchange import corpus; from railhead.exchange.bend import normalize;"
            "cs = corpus.generate('sessions', 9, 8) + corpus.generate('mutations', 9, 30) + corpus.edge();"
            "print(hashlib.sha256(''.join(normalize(c) for c in cs).encode()).hexdigest())")
    outs = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                           env={**os.environ, "PYTHONHASHSEED": h}).stdout for h in ("0", "1", "12345")}
    assert len(outs) == 1


def test_sessions_replay_ok():
    cases = corpus.generate("sessions", 11, 40)
    assert all(outcome_label(reference(c)) == "ok" for c in cases)
    assert all(corpus.session_run(random.Random(11 * 1_000_003 + i))[1] is None for i in range(10))


def test_corpus_coverage():
    """Every op, both arms and rules in sessions; every error code apply can raise across the corpus."""
    sessions = corpus.generate("sessions", 0, 150)
    cov = corpus.coverage(sessions)
    assert set(cov["ops"]) == set(corpus.RECORD_OPS)
    assert set(cov["arms"]) == {"enforced", "cheap_talk"} and set(cov["rules"]) == {"1", "2"}
    texts = [r["text"] for c in sessions for r in c if r["op"] == "msg"]
    assert any(len(t) == MAX_MESSAGE_CHARS for t in texts)
    assert any(any(ord(ch) > 0xFFFF for ch in t) for t in texts)
    assert any(any(0xD800 <= ord(ch) <= 0xDFFF for ch in t) for t in texts)
    assert any(isinstance(v, int) and v > 2 ** 64 for c in sessions for r in c for _, v in corpus._int_paths(r))
    outcomes = set(corpus.coverage(corpus.generate("mutations", 0, 1500) + corpus.edge())["outcomes"])
    codes = set(re.findall(r'ExchangeError\("([a-z_]+)"', "".join(p.read_text() for p in SRC.glob("*.py"))))
    assert codes - UNREACHABLE <= outcomes, sorted(codes - UNREACHABLE - outcomes)
    assert {"ok", "internal", "invariant"} <= outcomes


def test_mutants_cut_after_the_mutated_record():
    base = corpus.session(random.Random(5))
    for m in corpus.mutants_of(base, random.Random(6), k=20):
        assert m[:-1] == base[:len(m) - 1]


def test_licence_windows_stay_small():
    """A huge licence window would list ~2^60 instalments: no generator makes one."""
    cases = corpus.generate("sessions", 2, 60) + corpus.generate("mutations", 2, 300)
    for c in cases:
        for r in c:
            terms = r.get("terms") if isinstance(r, dict) else None
            if isinstance(terms, dict) and "rect" in terms:
                ws, dl = terms.get("window_start"), terms.get("deadline")
                if isinstance(ws, int) and isinstance(dl, int):
                    assert dl - ws < 2 ** 32


# ============================================================================================== binary plumbing
def _fake(tmp_path, body, name="fake-ex"):
    p = tmp_path / name
    p.write_text(f"#!{sys.executable}\nimport json, sys\n{body}\n")
    p.chmod(0o755)
    return p


FAKE_OK = ("from railhead.exchange.bend import reference\n"
           "print(reference([json.loads(l) for l in open(sys.argv[1]) if l.strip()]))")


def test_run_bend_and_compare_with_fake_binary(tmp_path):
    recs = _session_records(arm="cheap_talk", rules=2)
    ok = _fake(tmp_path, FAKE_OK)
    assert run_bend(recs, ok) == reference(recs)
    res = compare(recs, ok)
    assert res.match and res.n_records == len(recs)
    wrong = _fake(tmp_path, "print('{\"ok\":{\"tick\":-1}}')", "wrong")
    res = compare(recs, wrong)
    assert not res.match and res.diff.startswith("$.ok.")


@pytest.mark.parametrize("body, msg", [
    ("sys.exit(3)", "exit 3"),
    ("print('a'); print('b')", "exactly one line"),
    ("sys.stdout.write('{}')", "exactly one line"),
    ("import time; time.sleep(5)", "timed out"),
])
def test_run_bend_rejects_bad_binaries(tmp_path, body, msg):
    with pytest.raises(BendError, match=msg):
        run_bend([], _fake(tmp_path, body), timeout=2)


def test_cli_verify_and_corpus(tmp_path, capsys, monkeypatch):
    recs = _session_records()
    run = tmp_path / "run1" / "coord" / "run"
    run.mkdir(parents=True)
    _write_merged(run / "actions.jsonl", recs)
    plain = tmp_path / "plain.jsonl"
    plain.write_text(normalize(recs[:2]))
    ok = _fake(tmp_path, FAKE_OK)
    assert admin_main(["waybill-bend", "verify", str(tmp_path / "run1"), str(plain), "--binary", str(ok)]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [f"{tmp_path / 'run1'}: match {len(recs)}", f"{plain}: match 2"]
    wrong = _fake(tmp_path, "print('{}')", "wrong")
    assert admin_main(["waybill-bend", "verify", str(plain), "--binary", str(wrong)]) == 1
    assert "DIFF" in capsys.readouterr().out
    monkeypatch.setenv("RAILHEAD_BEND_EX", str(ok))
    assert admin_main(["waybill-bend", "verify", str(tmp_path / "missing")]) == 1
    assert "ERROR" in capsys.readouterr().out

    out_dir = tmp_path / "corpus"
    assert admin_main(["waybill-bend", "corpus", "--out", str(out_dir), "--kind", "edge", "--count", "7"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["edge"]["cases"] == 7
    inputs = sorted(out_dir.glob("case-*.jsonl"))
    assert len(inputs) == 7
    for p in inputs:
        assert p.with_suffix(".expected").read_text() == reference(extract_ex_records(p)) + "\n"
    assert admin_main(["waybill-bend", "corpus", "--kind", "edge"]) == 2


def test_capture_plugin(tmp_path):
    """The conftest hook records accepted sequences and rejected records (one small test file)."""
    out = corpus.run_capture(tmp_path, tests=("tests/unit/test_exchange_lifecycle.py",))
    cases = corpus.captured(None, tmp_path)
    assert out.is_file() and len(cases) > 10
    labels = {outcome_label(reference(c)) for c in cases}
    assert "ok" in labels and len(labels) > 3
    assert corpus.captured(3, out) == cases[:3]


# ============================================================================================== differential
BINARY = find_binary()


@pytest.mark.skipif(BINARY is None or not Path(BINARY).is_file(),
                    reason="no Bend binary (set RAILHEAD_BEND_EX or build bend/build/waybill-ex)")
class TestDifferential:
    """Byte equality of the Bend binary and the Python reference over a few hundred corpus cases."""

    def _check(self, cases):
        bad = []
        for i, c in enumerate(cases):
            res = compare(c, BINARY)
            if not res.match:
                bad.append((i, res.diff))
        assert not bad, bad[:10]

    def test_edge(self):
        self._check(corpus.edge())

    def test_sessions(self):
        self._check(corpus.generate("sessions", 0, 100))

    def test_mutations(self):
        self._check(corpus.generate("mutations", 0, 300))

    def test_captured(self, tmp_path):
        cases = corpus.captured(None, corpus.run_capture(tmp_path))
        self._check(cases[::max(1, len(cases) // 200)])
