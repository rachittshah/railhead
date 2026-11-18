"""The Waybill analysis pipeline on synthetic runs with known answers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from railhead.multi import analysis as an

SEATS = [{"slot": 0, "vendor": "claude", "model": "claude-opus-5-5"}, {"slot": 1, "vendor": "codex",
                                                                      "model": "gpt-6.1-terra"}]


def ob(i, payer, payee, amount, paid, status="kept", collected=0, due=100, settle=200, payments=None):
    return {"id": f"O{i}", "contract_id": "C1", "kind": "licence_instalment", "payer": payer, "payee": payee,
            "amount": amount, "paid": paid, "collected": collected, "due": due, "settle_tick": settle,
            "status": status, "payments": payments if payments is not None else ([[150, paid]] if paid else [])}


def supply(cid, seller, q, delivered, outcome, tender=False, signed=10, settled=500, price=1000):
    return {"id": cid, "kind": "supply", "tender_id": cid if tender else None,
            "terms": {"seller": seller, "buyer": "exchange" if tender else "1", "quantity": q, "price": price},
            "signed_tick": signed, "status": "settled", "outcome": outcome, "settlement": {"delivered": delivered},
            "events": [{"event": "offer"}, {"event": "settle", "tick": settled}]}


def make_run(root: Path, name: str, seed: int, arm: str, obligations=(), contracts=(), scores=(100, 200),
             clean=True, model_used=("claude-opus-5-5", "gpt-6.1-terra"), ended="horizon"):
    d = root / name
    (d / "verify").mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"task_id": f"waybill-short-s{seed:04d}-r0-{arm}", "arm": arm,
                                            "task_dir": str(root / "none"), "seats": SEATS}))
    comps = [{"slot": i, "id": i, "name": f"C{i}", "model_used": model_used[i], "score": scores[i],
              "net_worth": scores[i], "ledger_balance": 0} for i in range(2)]
    (d / "result.json").write_text(json.dumps({"verify": {"clean": clean, "replay_equals_live": True},
                                               "run": {"ended": ended, "runner_submits": 0}, "companies": comps}))
    ex = {"obligations": {o["id"]: o for o in obligations}, "contracts": {c["id"]: c for c in contracts},
          "tenders": {}, "messages": []}
    (d / "verify" / "scores.json").write_text(json.dumps({"market": {"exchange": ex}, "companies": comps}))
    return d


def test_wilcoxon_exact_and_holm_known_answers():
    w = an.wilcoxon([1, 2, 3, 4, 5, 6])
    assert w["w_plus"] == 21 and w["p"] == pytest.approx(2 / 64)
    assert an.wilcoxon([1, -2, 3, -4, 5, -6])["p"] > 0.5  # balanced signs
    small = an.wilcoxon([1, 2, 3])
    assert small["testable"] is False and small["p"] is None
    tied = an.wilcoxon([1, 1, 1, 1, 1, 1, 0])  # the zero is dropped; all ties
    assert tied["n_nonzero"] == 6 and tied["p"] == pytest.approx(2 / 64)
    big = an.wilcoxon([i + 0.5 for i in range(30)])
    assert big["p"] < 1e-5
    h = an.holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
    assert h["a"]["reject"] and h["a"]["p_holm"] == pytest.approx(0.03)
    assert not h["c"]["reject"] and h["c"]["p_holm"] == pytest.approx(0.06) and not h["b"]["reject"]
    assert h["d"]["p_holm"] is None


def test_h1a_repayment_by_family_with_a_bootstrap_over_seeds(tmp_path):
    runs = []
    for seed, (claude_paid, codex_paid) in {1: (50, 100), 2: (100, 100), 3: (0, 100)}.items():
        cheap = [ob(1, "0", "1", 100, claude_paid, "kept" if claude_paid == 100 else "partial" if claude_paid else "broken"),
                 ob(2, "1", "0", 100, codex_paid), ob(3, "0", "exchange", 999, 0, "broken")]  # tender: not a2a
        runs.append(make_run(tmp_path, f"s{seed}-cheap", seed, "cheap_talk", obligations=cheap))
        runs.append(make_run(tmp_path, f"s{seed}-enf", seed, "enforced",
                             obligations=[ob(1, "0", "1", 100, 0, collected=100)]))
    res = an.run(runs, tmp_path / "out", boot=500, make_figures=False)
    h = res["H1a"]
    assert h["overall"]["estimate"] == pytest.approx((150 + 300) / 600)
    assert h["by_family"]["claude"]["estimate"] == pytest.approx(150 / 300)
    assert h["by_family"]["codex"]["estimate"] == 1.0
    lo, hi = h["overall"]["ci95"]
    assert lo <= 0.75 <= hi and lo >= 0.5
    assert h["timing_counts"] == {"on_time": 4, "late": 0, "partial": 1, "unpaid": 1}
    assert res["paired_seeds"] == 3 and res["underpowered"]
    rows = (tmp_path / "out" / "obligations.csv").read_text().splitlines()
    assert len(rows) == 1 + 3 * 3 + 3  # header + cheap (3 each) + enforced (1 each)


def test_paired_tests_and_exclusions(tmp_path):
    runs = []
    for seed in range(1, 8):  # 7 seeds; enforced welfare always higher
        runs.append(make_run(tmp_path, f"s{seed}-enf", seed, "enforced", scores=(200 + seed, 300),
                             contracts=[supply("T1", "0", 100, 80, "partial", tender=True)]))
        runs.append(make_run(tmp_path, f"s{seed}-cheap", seed, "cheap_talk", scores=(100, 200),
                             contracts=[supply("T1", "0", 100, 40, "partial", tender=True)]))
    runs.append(make_run(tmp_path, "s9-enf", 9, "enforced", clean=False))  # excluded with its pair
    runs.append(make_run(tmp_path, "s9-cheap", 9, "cheap_talk"))
    runs.append(make_run(tmp_path, "s10-enf", 10, "enforced", model_used=("claude-sonnet-5-5", "gpt-6.1-terra")))
    runs.append(make_run(tmp_path, "s10-cheap", 10, "cheap_talk", ended="usd_cap"))
    res = an.run(runs, tmp_path / "out", boot=300, make_figures=False)
    ex = {e["seed"]: e for e in res["exclusions"]}
    assert set(ex) == {9, 10} and ex[9]["reasons"] == {"enforced": ["verify not clean"]}
    assert "ran claude-sonnet-5-5" in ex[10]["reasons"]["enforced"][0] and ex[10]["reasons"]["cheap_talk"] == \
        ["ended by usd_cap"]
    assert "s9-cheap" not in res["included_runs"] and len(res["included_runs"]) == 14
    h3 = res["H3"]
    assert h3["n_nonzero"] == 7 and h3["p"] == pytest.approx(2 / 128) and h3["median_difference"] == 204
    h1b = res["H1b"]
    assert h1b["median_difference"] == pytest.approx(0.4) and h1b["p"] == pytest.approx(2 / 128)
    assert res["H2"]["breach_rate"]["estimate"] == 1.0  # every enforced tender ended partial
    assert res["holm"]["result"]["H3"]["reject"] and res["holm"]["tests"] == ["H1b", "H2", "H3"]
    assert json.loads((tmp_path / "out" / "exclusions.json").read_text())


def test_power_estimate_and_one_seed_does_not_crash(tmp_path):
    cheap = [ob(1, "0", "1", 100, 60, "partial"), ob(2, "1", "0", 100, 100)]
    runs = [make_run(tmp_path, "c", 1, "cheap_talk", obligations=cheap), make_run(tmp_path, "e", 1, "enforced")]
    res = an.run(runs, tmp_path / "out", boot=200, make_figures=True)
    assert res["H1a"]["overall"]["ci95"] is None and "one seed" in res["H1a"]["overall"]["note"]
    assert res["H1c"]["testable"] is False and "not testable" in res["H1c"]["note"]
    p = res["H1c"]["power"]
    assert p["observed_difference"] == pytest.approx(-0.4) and p["mde_80pct_at_30_seeds"] is not None
    assert an.power_paired(1.0, 1.0, 30, sims=300) > 0.9 and an.power_paired(0.0, 1.0, 30, sims=300) < 0.2
    pytest.importorskip("matplotlib")
    assert {Path(f).name for f in res["figures"]} == {"h1a_repayment.svg", "h1a_timing.svg", "paired_differences.svg"}


def licence(cid, grantor, grantee, signed=10):
    return {"id": cid, "kind": "licence", "terms": {"grantor": grantor, "grantee": grantee}, "signed_tick": signed,
            "status": "settled" if signed is not None else "expired", "events": []}


def add_deliveries(run: Path, horizon, end, needs=((11, 3), (7, 1)), ok=True, log=b'{"kind":"header"}\n'):
    import hashlib

    (run / "coord" / "run").mkdir(parents=True, exist_ok=True)
    (run / "coord" / "run" / "actions.jsonl").write_bytes(log)
    doc = {"version": 1, "log_sha256": hashlib.sha256(log).hexdigest(), "check": {"ok": ok, "mismatches": []},
           "needs": [list(n) for n in needs], "deliveries": {"log_end": horizon, "horizon": horizon, "end": end}}
    (run / "deliveries.json").write_text(json.dumps(doc))


def test_h3_cargo_and_h4_market_share_from_deliveries(tmp_path):
    runs = []
    for seed in range(1, 8):
        e = make_run(tmp_path, f"s{seed}-enf", seed, "enforced", contracts=[licence("C1", "0", "1")])
        c = make_run(tmp_path, f"s{seed}-cheap", seed, "cheap_talk",
                     contracts=[licence("C1", "0", "1", signed=None)])
        # enforced: the licence pair 0+1 supplies both markets; cheap talk: no signed licence, less cargo
        add_deliveries(e, [[0, 3, 11, 50]], [[0, 3, 11, 90 + seed], [1, 1, 7, 10], [0, 9, 99, 1000]])
        add_deliveries(c, [[0, 3, 11, 20]], [[0, 3, 11, 40], [1, 1, 7, 10]])
        runs += [e, c]
    res = an.run(runs, tmp_path / "out", boot=300, make_figures=False)
    cm = res["H3"]["cargo_measure"]
    assert cm["end"]["by_seed"]["?:1"] == {"enforced": 101, "cheap_talk": 50}  # (99, 9) is not a need
    assert cm["end"]["median_difference"] == 54 and cm["end"]["p"] == pytest.approx(2 / 128)
    assert cm["horizon"]["median_difference"] == 30
    assert res["holm"]["tests"] == ["H1b", "H2", "H3"] and res["H3"]["n_nonzero"] == 0  # the test stays on scores
    ms = res["H4"]["market_share_measure"]
    assert ms["end"]["by_seed"]["?:1"] == {"enforced": 1.0, "cheap_talk": 0.0}
    assert ms["end"]["median_difference"] == 1.0 and ms["runs"]["s1-enf"]["licence_pairs"] == [[0, 1]]
    rows = (tmp_path / "out" / "markets.csv").read_text().splitlines()
    assert rows[0].startswith("run,track,seed,arm,window,cargo,industry") and len(rows) == 1 + 7 * (3 + 2 + 1 + 1)


def test_deliveries_missing_stale_or_failed_are_not_used(tmp_path):
    e = make_run(tmp_path, "e", 1, "enforced")
    c = make_run(tmp_path, "c", 1, "cheap_talk")
    res = an.run([e, c], tmp_path / "out", boot=100, make_figures=False)
    assert "no run has a usable deliveries.json" in res["H3"]["cargo_measure"]["note"]
    add_deliveries(e, [], [[0, 3, 11, 5]], ok=False)
    add_deliveries(c, [], [[0, 3, 11, 5]])
    (c / "coord" / "run" / "actions.jsonl").write_text('{"kind":"header","changed":1}\n')
    res = an.run([e, c], tmp_path / "out2", boot=100, make_figures=False)
    miss = res["H4"]["market_share_measure"]["missing"]
    assert miss == {"e": "deliveries.json failed its check against the verified run",
                    "c": "deliveries.json is for another log"}
