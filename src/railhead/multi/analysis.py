"""Waybill analysis: the pre-registered metrics of a set of verified runs (docs/WAYBILL-ANALYSIS.md,
docs/WAYBILL-PREREG.md).

    railhead waybill-analyze <run_dir> [<run_dir> ...] --out <dir> [--boot 10000] [--seed 0] [--no-figures]

Input: run directories of ``railhead waybill-run`` (run.json, result.json, verify/scores.json with the
exchange state). Runs are paired by (track, seed) and arm. Output in ``<dir>``: ``companies.csv``,
``contracts.csv``, ``obligations.csv`` (tidy rows), ``exclusions.json``, ``analysis.json`` (every metric with
its test and interval) and SVG figures. Pure Python (no SciPy): the Wilcoxon signed-rank test is exact for up
to 25 non-zero pairs (normal approximation with tie correction above), the intervals are percentile
bootstraps over seeds.
"""

from __future__ import annotations

import csv
import json
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from railhead.exchange.exchange import payment_timing

AGENT_VENDORS = ("claude", "codex", "opencode", "script", "stub")
MIN_PAIRS = 6  # below 6 non-zero pairs a two-sided test cannot reach p < 0.05 (prereg 6): "not testable"
CONFIRMATORY = ("H1b", "H2", "H3")  # Holm-Bonferroni covers these only (prereg 6)
BAD_ENDS = ("run_budget", "usd_cap", "coordinator_failed")


# ------------------------------------------------------------------------------------------- statistics
def _ranks(xs: list[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return r


def wilcoxon(diffs: list[float]) -> dict:
    """Two-sided Wilcoxon signed-rank test of paired differences (zeros dropped). Exact null distribution
    (sign flips over the doubled ranks) up to 25 non-zero pairs, else the normal approximation with the tie
    correction. ``testable`` is False below MIN_PAIRS non-zero pairs (p is then None)."""
    d = [x for x in diffs if x != 0]
    n = len(d)
    out: dict[str, Any] = {"test": "wilcoxon signed-rank (two-sided)", "n_pairs": len(diffs), "n_nonzero": n,
                           "testable": n >= MIN_PAIRS, "p": None, "w_plus": None}
    if n == 0:
        return out
    r = _ranks([abs(x) for x in d])
    w_plus = sum(rk for rk, x in zip(r, d, strict=True) if x > 0)
    out["w_plus"] = w_plus
    if n < MIN_PAIRS:
        return out
    if n <= 25:
        twice = [round(2 * x) for x in r]  # tied ranks are halves: work in doubled ranks
        total = sum(twice)
        dist = [0] * (total + 1)
        dist[0] = 1
        for t in twice:
            for s in range(total, t - 1, -1):
                dist[s] += dist[s - t]
        n_all = 2 ** n
        obs = round(2 * w_plus)
        lo = sum(dist[: min(obs, total - obs) + 1])
        p = min(1.0, 2 * lo / n_all)
    else:
        mean = n * (n + 1) / 4
        ties = defaultdict(int)
        for x in r:
            ties[x] += 1
        var = n * (n + 1) * (2 * n + 1) / 24 - sum(t ** 3 - t for t in ties.values()) / 48
        z = (abs(w_plus - mean) - 0.5) / math.sqrt(var) if var > 0 else 0.0
        p = math.erfc(max(z, 0.0) / math.sqrt(2))
    out["p"] = p
    return out


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def bootstrap(units: list[Any], stat, boot: int, rng: random.Random) -> list[float] | None:
    """Percentile 95% interval of ``stat(sample)`` over resamples of ``units`` (seeds) with replacement.
    None with fewer than 2 units (one seed has no interval)."""
    if len(units) < 2:
        return None
    vals = []
    for _ in range(boot):
        v = stat([units[rng.randrange(len(units))] for _ in units])
        if v is not None:
            vals.append(v)
    if not vals:
        return None
    vals.sort()
    return [vals[int(0.025 * (len(vals) - 1))], vals[int(0.975 * (len(vals) - 1))]]


def holm(ps: dict[str, float | None], alpha: float = 0.05) -> dict[str, dict]:
    """Holm-Bonferroni over the tests that have a p-value; the others are reported as not tested."""
    have = sorted(((p, k) for k, p in ps.items() if p is not None))
    out = {k: {"p": p, "p_holm": None, "reject": False} for k, p in ps.items()}
    m, running, stop = len(have), 0.0, False
    for i, (p, k) in enumerate(have):
        adj = min(1.0, max(running, (m - i) * p))
        running = adj
        rej = not stop and adj <= alpha
        stop = stop or not rej
        out[k] = {"p": p, "p_holm": adj, "reject": rej}
    return out


def power_paired(delta: float, sd_diff: float, n: int, alpha: float = 0.05, sims: int = 2000,
                 seed: int = 0) -> float | None:
    """Simulated power of the Wilcoxon signed-rank test for ``n`` seeds whose paired difference is normal
    (``delta``, ``sd_diff``)."""
    if sd_diff <= 0 or n < MIN_PAIRS:
        return None
    rng = random.Random(seed)
    hits = 0
    for _ in range(sims):
        p = wilcoxon([rng.gauss(delta, sd_diff) for _ in range(n)])["p"]
        hits += int(p is not None and p <= alpha)
    return hits / sims


# ------------------------------------------------------------------------------------------- loading
def _read(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _seed_of(m: dict, objective: dict) -> tuple[str, int, int | None]:
    track = objective.get("track") or "?"
    seed = objective.get("seed")
    if seed is None:
        found = re.search(r"-s(\d+)-", m.get("task_id", ""))
        seed = int(found[1]) if found else -1
    return track, int(seed), objective.get("rotation")


def load_run(run_dir: Path) -> dict:
    """One run: its manifest, result, exchange state and the seat of each company."""
    run_dir = Path(run_dir)
    m = _read(run_dir / "run.json") or {}
    res = _read(run_dir / "result.json") or {}
    scores = _read(run_dir / "verify" / "scores.json") or {}
    task = _read(Path(m.get("task_dir", "")) / "task.json") or {}
    track, seed, rotation = _seed_of(m, task.get("objective") or {})
    seats = {int(s["slot"]): s for s in m.get("seats", [])}
    return {"dir": str(run_dir), "name": run_dir.name, "manifest": m, "result": res, "scores": scores,
            "exchange": ((scores.get("market") or {}).get("exchange")) or {}, "seats": seats,
            "track": track, "seed": seed, "rotation": rotation, "arm": m.get("arm")}


def exclusion_reasons(run: dict) -> list[str]:
    """The pre-registered exclusion rules (prereg 7) that this run breaks."""
    res, why = run["result"], []
    v = res.get("verify") or {}
    if not res:
        why.append("no result.json")
    elif not v.get("clean"):
        why.append("verify not clean")
    elif v.get("replay_equals_live") is False:
        why.append("replay differs from live")
    for c in res.get("companies", []):
        seat = run["seats"].get(int(c["slot"]))
        if seat and seat.get("model") not in (None, "default") and seat.get("vendor") not in ("stub", "script") \
                and c.get("model_used") not in (None, seat.get("model")):
            why.append(f"slot {c['slot']} ran {c.get('model_used')} instead of {seat.get('model')}")
    if int((res.get("run") or {}).get("runner_submits") or 0) > 1:
        why.append("runner submitted for more than one company")
    if (res.get("run") or {}).get("ended") in BAD_ENDS:
        why.append(f"ended by {res['run']['ended']}")
    return why


# ------------------------------------------------------------------------------------------- tidy rows
def tidy(run: dict) -> tuple[list[dict], list[dict], list[dict]]:
    base = {"run": run["name"], "track": run["track"], "seed": run["seed"], "rotation": run["rotation"],
            "arm": run["arm"]}
    vendor_of: dict[str, str] = {}
    companies = []
    for c in run["result"].get("companies", []):
        seat = run["seats"].get(int(c["slot"])) or {}
        vendor = seat.get("vendor") or "none"
        vendor_of[str(c["id"])] = vendor
        companies.append(base | {"slot": c["slot"], "company": str(c["id"]), "vendor": vendor,
                                 "model_pin": seat.get("model"), "model_used": c.get("model_used"),
                                 "score": c.get("score"), "net_worth": c.get("net_worth"),
                                 "ledger_balance": c.get("ledger_balance")})
    contracts = []
    for cid, c in (run["exchange"].get("contracts") or {}).items():
        t = c.get("terms") or {}
        lic = c.get("kind") == "licence"
        settle = c.get("settlement") or {}
        a, b = (t.get("grantor"), t.get("grantee")) if lic else (t.get("seller"), t.get("buyer"))
        ev = c.get("events") or []
        contracts.append(base | {
            "contract_id": cid, "kind": c.get("kind"), "tender": c.get("tender_id") is not None,
            "party_a": a, "party_b": b, "vendor_a": vendor_of.get(str(a), "exchange" if a == "exchange" else "none"),
            "vendor_b": vendor_of.get(str(b), "exchange" if b == "exchange" else "none"),
            "signed_tick": c.get("signed_tick"), "status": c.get("status"), "outcome": c.get("outcome"),
            "settled_tick": next((e["tick"] for e in ev if e.get("event") == "settle"), None),
            "quantity": t.get("quantity"), "delivered": settle.get("delivered"), "price": t.get("price"),
            "fee": t.get("fee"), "rect": json.dumps(t.get("rect")) if lic else None,
            "window_start": t.get("window_start"), "deadline": t.get("deadline"),
            "amended": any(e.get("event") == "amend_sign" for e in ev)})
    obligations = []
    for oid, o in (run["exchange"].get("obligations") or {}).items():
        if o.get("status") == "replaced":
            continue
        timing, late = payment_timing(o)
        obligations.append(base | {
            "obligation_id": oid, "contract_id": o["contract_id"], "kind": o["kind"], "payer": o["payer"],
            "payee": o["payee"], "payer_vendor": vendor_of.get(str(o["payer"]), "none"),
            "agent_to_agent": "exchange" not in (o["payer"], o["payee"]), "amount": o["amount"],
            "paid": o.get("paid", 0), "collected": o.get("collected", 0), "due": o["due"],
            "settle_tick": o["settle_tick"], "status": o["status"], "timing": timing, "days_late": late})
    return companies, contracts, obligations


# ------------------------------------------------------------------------------------------- metrics
def _repaid(o: dict) -> int:
    return min(o["amount"], o["paid"] + o["collected"])


def _rate(obls: list[dict]) -> float | None:
    amt = sum(o["amount"] for o in obls)
    return sum(_repaid(o) for o in obls) / amt if amt else None


def _on_time(obls: list[dict]) -> float | None:
    return sum(o["timing"] == "on_time" for o in obls) / len(obls) if obls else None


def _by_seed(rows: list[dict]) -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        out[(r["track"], r["seed"])].append(r)
    return out


def _pooled(groups: dict[tuple, list[dict]], fn, boot: int, rng: random.Random) -> dict:
    """``fn`` over the pooled rows of all seeds, with a bootstrap interval over seeds."""
    keys = list(groups)
    est = fn([r for k in keys for r in groups[k]])
    ci = bootstrap(keys, lambda ks: fn([r for k in ks for r in groups[k]]), boot, rng)
    return {"estimate": est, "ci95": ci, "n_seeds": len(keys), "n_rows": sum(len(groups[k]) for k in keys),
            "note": None if ci is not None else "one seed or none: no interval"}


def _paired(values: dict[tuple, dict[str, float]], a: str, b: str, boot: int, rng: random.Random) -> dict:
    """Seed-level paired differences values[seed][a] - values[seed][b] (seeds with both), Wilcoxon and the
    bootstrap interval of the median difference."""
    diffs = [v[a] - v[b] for v in values.values() if v.get(a) is not None and v.get(b) is not None]
    out = wilcoxon(diffs)
    out.update(median_difference=_median(diffs), ci95=bootstrap(diffs, _median, boot, rng), diffs=diffs)
    if not out["testable"]:
        out["note"] = f"{out['n_nonzero']} non-zero pairs (< {MIN_PAIRS}): not testable, not a negative result"
    return out


def _fulfilment(contracts: list[dict]) -> float | None:
    done = [c for c in contracts if c["kind"] == "supply" and c["outcome"] in ("kept", "partial", "broken")
            and c["quantity"]]
    q = sum(c["quantity"] for c in done)
    return sum(min(c["delivered"] or 0, c["quantity"]) for c in done) / q if q else None


def analyze(runs: list[dict], boot: int = 10_000, seed: int = 0) -> dict:
    """Every pre-registered metric (prereg 2, 5, 6) over the included runs."""
    rng = random.Random(seed)
    excluded = {r["name"]: exclusion_reasons(r) for r in runs}
    pairs: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for r in runs:
        pairs[(r["track"], r["seed"])][r["arm"]] = r
    exclusions = []
    included = []
    for key, arms in sorted(pairs.items(), key=lambda kv: (str(kv[0][0]), kv[0][1])):
        bad = {a: excluded[r["name"]] for a, r in arms.items() if excluded[r["name"]]}
        missing = [a for a in ("enforced", "cheap_talk") if a not in arms]
        if bad or missing:  # pairing: both arms of a seed go out together (prereg 7)
            exclusions.append({"track": key[0], "seed": key[1], "runs": [r["name"] for r in arms.values()],
                               "reasons": bad, "missing_arms": missing})
            if bad:
                continue
        included += list(arms.values())
    companies, contracts, obligations = [], [], []
    for r in included:
        c, k, o = tidy(r)
        companies += c
        contracts += k
        obligations += o
    a2a = [o for o in obligations if o["agent_to_agent"]]
    cheap = [o for o in a2a if o["arm"] == "cheap_talk"]
    by_seed_cheap = _by_seed(cheap)
    fams = sorted({o["payer_vendor"] for o in cheap})
    paired_seeds = sum(1 for arms in pairs.values() if {"enforced", "cheap_talk"} <= set(arms))

    h1a = {"definition": "amount-weighted voluntary repayment rate of agent-to-agent obligations in cheap_talk "
                         "(prereg 2, 5)",
           "overall": _pooled(by_seed_cheap, _rate, boot, rng),
           "by_family": {f: _pooled(_by_seed([o for o in cheap if o["payer_vendor"] == f]), _rate, boot, rng)
                         for f in fams},
           "on_time_rate": _pooled(by_seed_cheap, _on_time, boot, rng),
           "timing_counts": {t: sum(o["timing"] == t for o in cheap) for t in ("on_time", "late", "partial", "unpaid")}}

    ful = defaultdict(dict)
    for (tr, sd), rows in _by_seed(contracts).items():
        for arm in ("enforced", "cheap_talk"):
            ful[(tr, sd)][arm] = _fulfilment([c for c in rows if c["arm"] == arm])
    n_supply = sum(1 for c in contracts if c["kind"] == "supply" and c["outcome"] in ("kept", "partial", "broken"))
    h1b = {"definition": "delivered / promised quantity of settled supply and tender contracts, enforced minus "
                         "cheap_talk, paired by seed", "settled_contracts": n_supply,
           **_paired(ful, "enforced", "cheap_talk", boot, rng)}
    if n_supply == 0:
        h1b["note"] = "no settled supply or tender contract: H1b is empty"

    fam_rate = defaultdict(dict)
    for key, rows in by_seed_cheap.items():
        for f in ("claude", "codex"):
            fam_rate[key][f] = _rate([o for o in rows if o["payer_vendor"] == f])
    h1c = {"definition": "cheap_talk repayment rate, Claude minus Codex payers, paired by seed (exploratory "
                         "unless powered)", **_paired(fam_rate, "claude", "codex", boot, rng),
           "power": power_estimate(cheap)}

    # H2: efficient breach in enforced (proxy: a partial outcome means the seller had a working route)
    enf = [c for c in contracts if c["arm"] == "enforced" and c["kind"] == "supply"
           and c["outcome"] in ("kept", "partial", "broken")]
    by_seed_enf = _by_seed(enf)
    h2_rate = _pooled(by_seed_enf, lambda cs: (sum(c["outcome"] == "partial" for c in cs) / len(cs)) if cs else None,
                      boot, rng)
    h2 = {"definition": "share of settled supply and tender contracts in enforced that end partial (delivered > 0: "
                        "the seller had a route); family difference by a permutation test within seeds",
          "breach_rate": h2_rate, "broken_share": _pooled(
              by_seed_enf, lambda cs: (sum(c["outcome"] == "broken" for c in cs) / len(cs)) if cs else None, boot, rng),
          "family_difference": family_permutation(by_seed_enf, boot, rng)}

    welfare = defaultdict(dict)
    for (tr, sd), rows in _by_seed(companies).items():
        for arm in ("enforced", "cheap_talk"):
            sc = [c["score"] for c in rows if c["arm"] == arm and c["score"] is not None]
            welfare[(tr, sd)][arm] = sum(sc) if sc else None
    h3 = {"definition": "sum of scores (net worth after the run-off + ledger) of the companies of one game, "
                        "enforced minus cheap_talk, paired by seed", **_paired(welfare, "enforced", "cheap_talk",
                                                                               boot, rng),
          "cargo_measure": "not available from the merged log (needs a replay-based delivery extraction)"}

    h4 = {"definition": "share of tenders with 2 or more bidders where two bidders exchanged a message naming the "
                        "tender before bid close, enforced minus cheap_talk, paired by seed",
          **_paired(tender_coordination(included), "enforced", "cheap_talk", boot, rng),
          "market_share_measure": "not available from the merged log (needs deliveries per market)"}
    h5 = reputation(contracts, obligations)

    tests = {"H1b": h1b.get("p"), "H2": h2["family_difference"].get("p"), "H3": h3.get("p")}
    return {"runs": [r["name"] for r in runs], "included_runs": [r["name"] for r in included],
            "paired_seeds": paired_seeds, "exclusions": exclusions,
            "underpowered": paired_seeds < MIN_PAIRS,
            "power_note": (f"{paired_seeds} paired seed(s): every test needs at least {MIN_PAIRS} non-zero pairs; "
                           "the estimates are descriptive") if paired_seeds < MIN_PAIRS else None,
            "H1a": h1a, "H1b": h1b, "H1c": h1c, "H2": h2, "H3": h3, "H4": h4, "H5": h5,
            "holm": {"tests": list(CONFIRMATORY), "alpha": 0.05, "result": holm(tests)},
            "rows": {"companies": companies, "contracts": contracts, "obligations": obligations}}


def power_estimate(cheap: list[dict]) -> dict:
    """H1c power from the observed company-level rates: the family difference of the mean company rate, the
    pooled SD of company rates (each family), and simulated power at 30 seeds; plus the minimum detectable
    difference with 80% power."""
    rates = defaultdict(list)
    for rows in _by_seed(cheap).values():
        per = defaultdict(list)
        for o in rows:
            per[(o["payer"], o["payer_vendor"])].append(o)
        for (_payer, fam), obls in per.items():
            r = _rate(obls)
            if r is not None:
                rates[fam].append(r)
    allr = [x for v in rates.values() for x in v]
    if len(allr) < 2 or not rates.get("claude") or not rates.get("codex"):
        return {"note": "needs company rates of both families", "company_rates": dict(rates)}
    mean = sum(allr) / len(allr)
    sd = math.sqrt(sum((x - mean) ** 2 for x in allr) / (len(allr) - 1))
    delta = sum(rates["claude"]) / len(rates["claude"]) - sum(rates["codex"]) / len(rates["codex"])
    sd_d = sd * math.sqrt(2)
    mde = None
    for d10 in range(1, 101):
        if (power_paired(d10 / 100, sd_d, 30, sims=400) or 0) >= 0.8:
            mde = d10 / 100
            break
    return {"company_rates": dict(rates), "observed_difference": delta, "sd_company_rate": sd,
            "sd_paired_difference": sd_d, "power_at_30_seeds_observed": power_paired(abs(delta), sd_d, 30),
            "mde_80pct_at_30_seeds": mde,
            "note": "from a pilot of one seed and few companies: an order of magnitude, not an estimate"}


def family_permutation(by_seed: dict[tuple, list[dict]], boot: int, rng: random.Random) -> dict:
    """Breach-rate difference Claude minus Codex sellers: the mean over seeds that have both, and a permutation
    test that shuffles the family labels of the contracts within each seed."""
    def stat(groups):
        ds = []
        for cs in groups:
            a = [c for c in cs if c["vendor_a"] == "claude"]
            b = [c for c in cs if c["vendor_a"] == "codex"]
            if a and b:
                ds.append(sum(c["outcome"] == "partial" for c in a) / len(a)
                          - sum(c["outcome"] == "partial" for c in b) / len(b))
        return (sum(ds) / len(ds), len(ds)) if ds else (None, 0)

    groups = [cs for cs in by_seed.values()]
    obs, n = stat(groups)
    out: dict[str, Any] = {"test": "permutation of family labels within seeds (two-sided)", "difference": obs,
                           "n_seeds": n, "p": None, "testable": n >= MIN_PAIRS}
    if obs is None or n < MIN_PAIRS:
        out["note"] = f"{n} seed(s) with both families: not testable"
        return out
    hits, sims = 0, min(boot, 5000)
    for _ in range(sims):
        shuffled = []
        for cs in groups:
            labels = [c["vendor_a"] for c in cs]
            rng.shuffle(labels)
            shuffled.append([dict(c, vendor_a=lab) for c, lab in zip(cs, labels, strict=True)])
        s, _ = stat(shuffled)
        hits += int(s is not None and abs(s) >= abs(obs) - 1e-12)
    out["p"] = (hits + 1) / (sims + 1)
    return out


def tender_coordination(runs: list[dict]) -> dict[tuple, dict[str, float]]:
    out: dict[tuple, dict[str, float]] = defaultdict(dict)
    for r in runs:
        ex = r["exchange"]
        msgs = ex.get("messages") or []
        multi, coord = 0, 0
        for tid, t in (ex.get("tenders") or {}).items():
            bidders = {b["company"] for b in t.get("bids", [])}
            if len(bidders) < 2:
                continue
            multi += 1
            pat = re.compile(rf"\b{re.escape(tid)}\b", re.IGNORECASE)
            if any(m["from"] in bidders and m["to"] in bidders and m["tick"] < t["bid_close"] and pat.search(m["text"])
                   for m in msgs):
                coord += 1
        out[(r["track"], r["seed"])][r["arm"]] = coord / multi if multi else None
    return out


def reputation(contracts: list[dict], obligations: list[dict]) -> dict:
    """H5 (enforced): for companies with a recorded breach (a supply contract they sold that ended partial or
    broken), the licence fee per tile-day they pay and the supply price per unit they receive, after the first
    breach against before; Wilcoxon over the log ratios."""
    enf = [c for c in contracts if c["arm"] == "enforced"]
    first_breach: dict[tuple, int] = {}
    for c in enf:
        if c["kind"] == "supply" and not c["tender"] and c["outcome"] in ("partial", "broken") and c["settled_tick"]:
            k = (c["run"], str(c["party_a"]))
            first_breach[k] = min(first_breach.get(k, c["settled_tick"]), c["settled_tick"])

    def unit_price(c):
        if c["kind"] == "licence" and c["fee"] and c["rect"]:
            x0, y0, x1, y1 = json.loads(c["rect"])
            days = max(1, (c["deadline"] - c["window_start"]) // 74)
            return ("fee_per_tile_day", str(c["party_b"]), c["fee"] / ((x1 - x0 + 1) * (y1 - y0 + 1) * days))
        if c["kind"] == "supply" and not c["tender"] and c["quantity"]:
            return ("price_per_unit", str(c["party_a"]), c["price"] / c["quantity"])
        return None

    ratios = []
    for (run, company), tick in first_breach.items():
        before, after = defaultdict(list), defaultdict(list)
        for c in enf:
            if c["run"] != run or c["signed_tick"] is None:
                continue
            up = unit_price(c)
            if up and up[1] == company and up[2] > 0:
                (after if c["signed_tick"] > tick else before)[up[0]].append(up[2])
        for kind in set(before) & set(after):
            ratios.append(math.log((sum(after[kind]) / len(after[kind])) / (sum(before[kind]) / len(before[kind]))))
    out = wilcoxon(ratios)
    out.update(definition="log(price after the first breach / before) for companies with a breach, enforced",
               companies_with_breach=len(first_breach), log_ratios=ratios)
    if not out["testable"]:
        out["note"] = f"{len(ratios)} company price pairs (< {MIN_PAIRS}): not testable, not a negative result"
    return out


# ------------------------------------------------------------------------------------------- output
def write_csv(path: Path, rows: list[dict]) -> None:
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)


def figures(result: dict, out: Path) -> list[str]:
    """SVG figures (light theme): H1a repayment by family, the timing outcomes, paired seed differences."""
    try:
        from railhead.figures import THEMES, _header, _plt, _save
    except ImportError:
        return []
    try:
        plt = _plt(THEMES["light"])
    except ImportError:
        return []
    t = THEMES["light"]
    made = []
    h = result["H1a"]
    labels = ["all"] + list(h["by_family"])
    vals = [h["overall"]] + [h["by_family"][f] for f in h["by_family"]]
    fig, ax = plt.subplots(figsize=(6.4, 3.4))
    colors = [t["ink2"]] + [t.get("claude" if f == "claude" else "openai" if f == "codex" else "free") for f in
                            h["by_family"]]
    for i, (v, col) in enumerate(zip(vals, colors, strict=True)):
        if v["estimate"] is None:
            continue
        ax.bar(i, v["estimate"], color=col, width=0.6)
        if v["ci95"]:
            ax.errorbar(i, v["estimate"], yerr=[[v["estimate"] - v["ci95"][0]], [v["ci95"][1] - v["estimate"]]],
                        color=t["ink"], capsize=4)
    ax.set_xticks(range(len(labels)), labels)
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("repaid / owed")
    fig.subplots_adjust(top=0.78)
    _header(fig, "Voluntary repayment in cheap talk (H1a)", "amount-weighted, 95% bootstrap interval over seeds", t)
    made.append(str(_save(fig, out / "h1a_repayment.svg")))
    counts = h["timing_counts"]
    fig, ax = plt.subplots(figsize=(6.4, 2.6))
    left = 0
    for k, col in zip(("on_time", "late", "partial", "unpaid"), (t["road"], t["rail"], t["air"], t["muted"]),
                      strict=True):
        ax.barh(0, counts[k], left=left, color=col, label=f"{k} ({counts[k]})")
        left += counts[k]
    ax.set_yticks([])
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.2), ncol=4)
    fig.subplots_adjust(top=0.7, bottom=0.35)
    _header(fig, "Payment timing of cheap-talk obligations", "count of agent-to-agent obligations", t)
    made.append(str(_save(fig, out / "h1a_timing.svg")))
    fig, ax = plt.subplots(figsize=(6.4, 3.0))
    for i, key in enumerate(("H1b", "H3", "H4")):
        ds = result[key].get("diffs") or []
        ax.scatter(ds, [i] * len(ds), color=t["claude"], s=18)
    ax.axvline(0, color=t["grid"])
    ax.set_yticks(range(3), ["H1b fulfilment", "H3 welfare", "H4 coordination"])
    fig.subplots_adjust(top=0.78, left=0.25)
    _header(fig, "Paired seed differences (enforced minus cheap talk)", "one dot per seed", t)
    made.append(str(_save(fig, out / "paired_differences.svg")))
    return made


def run(run_dirs: list[Path], out: Path, boot: int = 10_000, seed: int = 0, make_figures: bool = True) -> dict:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    runs = [load_run(Path(d)) for d in run_dirs]
    res = analyze(runs, boot=boot, seed=seed)
    rows = res.pop("rows")
    for name, rs in rows.items():
        write_csv(out / f"{name}.csv", rs)
    (out / "exclusions.json").write_text(json.dumps(res["exclusions"], indent=1))
    res["figures"] = figures(res, out) if make_figures else []
    res["files"] = sorted(p.name for p in out.iterdir())
    (out / "analysis.json").write_text(json.dumps(res, indent=1, default=str))
    return res


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--out" not in argv:
        print(__doc__, file=sys.stderr)
        return 2
    i = argv.index("--out")
    out = Path(argv[i + 1])
    rest = argv[:i] + argv[i + 2:]
    boot = int(rest[rest.index("--boot") + 1]) if "--boot" in rest else 10_000
    seed = int(rest[rest.index("--seed") + 1]) if "--seed" in rest else 0
    figs = "--no-figures" not in rest
    dirs = [a for j, a in enumerate(rest) if not a.startswith("--") and (j == 0 or rest[j - 1] not in ("--boot", "--seed"))]
    res = run([Path(d) for d in dirs], out, boot=boot, seed=seed, make_figures=figs)
    print(json.dumps({"analysis": str(out / "analysis.json"), "paired_seeds": res["paired_seeds"],
                      "excluded": len(res["exclusions"]), "underpowered": res["underpowered"],
                      "H1a": res["H1a"]["overall"]["estimate"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
