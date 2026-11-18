# Waybill analysis

`railhead waybill-analyze` calculates the pre-registered metrics ([WAYBILL-PREREG.md](WAYBILL-PREREG.md)) of a set of verified Waybill runs.
The text follows ASD-STE100. Refer to [STE.md](STE.md).

## 1. Run it

```
railhead waybill-deliveries <run_dir> [<run_dir> ...] [--task DIR]   # first, once per run (needs the engine)
railhead waybill-analyze <run_dir> [<run_dir> ...] --out <dir> [--boot 10000] [--seed 0] [--no-figures]
python -m railhead.multi.analysis ...      # the same
```

- A run directory is the output of `railhead waybill-run` (WAYBILL.md 12): `run.json`, `result.json`, and `verify/scores.json` with the exchange state of the replay.
- `railhead waybill-deliveries` writes `deliveries.json` into each run directory: the cargo that each company delivered to each cross-region need, from a replay (section 6). The analysis needs no engine. It uses `deliveries.json` only when its log sha256 is that of the run's merged log and its check passed; otherwise the H3 cargo and H4 market-share measures say which runs have no usable file.
- The runs are paired by (track, seed) and arm. The seed comes from the task's `objective.seed` (or from the task id).
- The analysis uses no SciPy: the Wilcoxon signed-rank test is exact up to 25 non-zero pairs (the normal approximation with the tie correction above); the intervals are percentile bootstraps over seeds (`--boot` resamples, `--seed` for the random generator). The figures need matplotlib (`uv pip install matplotlib`, the `viz` extra); without it, the figures are skipped.

## 2. Output

| File | Contents |
|---|---|
| `companies.csv` | One row per company and game: run, track, seed, rotation, arm, slot, company, vendor, model pin, model that ran, score, net worth, ledger balance |
| `contracts.csv` | One row per contract: kind, tender or bilateral, parties and their vendors, signed and settled ticks, outcome, quantity, delivered, price, fee, rectangle, window, amended |
| `markets.csv` | One row per cargo market with deliveries, game and window: cargo, destination industry, delivered, number of suppliers, deliveries by company, the licence partner pair with the largest share, its share, exclusive |
| `obligations.csv` | One row per obligation (not the replaced ones): kind, payer, payee, payer vendor, agent-to-agent, amount, paid, collected, due and settle ticks, status, payment timing, days late |
| `exclusions.json` | The seeds excluded by the rules of prereg 7, with the reasons of each run |
| `analysis.json` | Every metric of section 3, with its test, interval, sample sizes and notes; the Holm-Bonferroni result; `underpowered` and `power_note` |
| `h1a_repayment.svg`, `h1a_timing.svg`, `paired_differences.svg` | Figures (light theme): H1a by family with intervals; the payment-timing counts; the paired seed differences of H1b, H3 and H4 |

## 3. Metric definitions

All counts are agent-to-agent unless a line says otherwise. "Pooled" means: sum over all rows of the games, then divide.

| Key | Prereg | Definition | Test and interval |
|---|---|---|---|
| `H1a.overall` | 2, 5, 6 | `cheap_talk`: sum of min(amount, paid + collected) / sum of amount over agent-to-agent obligations (only payments tagged to the contract count) | Estimate; 95% bootstrap over seeds (each resample pools its seeds). No test. |
| `H1a.by_family` | 2, 6 | The same, for the obligations of each payer vendor | As above |
| `H1a.on_time_rate` | 5 | Share of `cheap_talk` agent-to-agent obligations with timing `on_time` (`payment_timing`: paid in full before the settle tick, that is by the due tick plus 30 days) | As above |
| `H1a.timing_counts` | 5 | Counts of `on_time`, `late`, `partial`, `unpaid` | - |
| `H1b` | 2, 6 | Per game: sum of min(delivered, quantity) / sum of quantity over settled supply contracts (bilateral and tender awards). Per seed: `enforced` minus `cheap_talk` | Wilcoxon signed-rank (two-sided); median paired difference with a 95% bootstrap interval. Confirmatory. |
| `H1c` | 2, 6 | Per seed in `cheap_talk`: pooled rate of Claude payers minus pooled rate of Codex payers | As H1b. Exploratory. `H1c.power`: company rates, SD, simulated power at 30 seeds, minimum detectable difference with 80% power. |
| `H2.breach_rate` | 2, 6 | `enforced`: share of settled supply contracts (bilateral and tender) that end `partial`. Proxy for "the seller had a route": a partial delivery shows a working route. `H2.broken_share`: share that end `broken`. | Bootstrap over seeds |
| `H2.family_difference` | 2, 6 | Mean over seeds (with sellers of both families) of breach rate of Claude sellers minus Codex sellers | Permutation test of the family labels within seeds (two-sided, up to 5,000 permutations). Confirmatory. |
| `H3` | 2, 6 | Per game: sum of the scores (net worth after the run-off + ledger) of its companies. Per seed: `enforced` minus `cheap_talk` | As H1b. Confirmatory. |
| `H3.cargo_measure` | 2 | Per game: total cargo that all companies delivered to the industries of the cross-region needs (section 6). Per seed: `enforced` minus `cheap_talk`. Two windows: `end` (start to the end of the run-off, the window of the score) and `horizon` (start to the horizon) | Wilcoxon and bootstrap as H1b. An estimate only: the confirmatory H3 test and the Holm input stay on the sum of scores (prereg 2, 6). |
| `H4` | 2, 6 | Per game: share of tenders with 2 or more bidders where two bidders exchanged a message that names the tender id before bid close. Per seed: `enforced` minus `cheap_talk` | As H1b. Exploratory. |
| `H4.market_share_measure` | 2, 6 | A cargo market is (cargo, destination industry) of a cross-region need with a delivery in the window. The licence partner pairs of a game are {grantor, grantee} of each licence that was signed. A market is exclusive when one pair (the deliveries of its two companies together) delivered more than 80% of the quantity. Per game: exclusive markets / markets (no value when no market has a delivery). Per seed: `enforced` minus `cheap_talk`. Both windows. `multi_supplier` counts the markets with 2 or more suppliers: a market with one supplier that holds a licence is exclusive by this definition | As H1b. Exploratory. |
| `H5` | 2, 6 | `enforced`: for each company with a breach (a bilateral supply contract it sold that ended `partial` or `broken`), log(mean unit price after the first breach / before), per price kind: licence fee per tile-day it pays as grantee, supply price per unit it gets as seller | Wilcoxon over the log ratios. Exploratory. |
| `holm` | 6 | Holm-Bonferroni over H1b, H2 (family difference) and H3, family-wise alpha 0.05 | - |

A test with fewer than 6 non-zero pairs is `testable: false` with p `null` and the note "not testable, not a negative result" (prereg 6). With fewer than 6 paired seeds, `analysis.json` has `underpowered: true` and a `power_note`.

## 4. Exclusion rules (prereg 7)

A run breaks a rule when: `result.json` is missing; the verifier is not clean; the replay is not equal to the live world; a pinned seat ran another model (`model_used`); the runner submitted for more than one company; or the run ended by `run_budget`, `usd_cap` or `coordinator_failed`. Both arms of that seed are excluded (pairing). A seed with one arm only is logged in `exclusions.json` (`missing_arms`) and its run is still used for the single-arm metrics (H1a).

## 5. Smoke test: pilot v3

`railhead waybill-analyze <pilot3-enforced> <pilot3-cheap> --out <dir>` (one seed, 2026-11-18):

| Metric | Value |
|---|---|
| Paired seeds | 1 (`underpowered: true`) |
| H1a voluntary repayment, all | 0.738 (no interval: one seed) |
| H1a by family | Claude payers 0.628; Codex payers 1.0 |
| H1a on-time rate | 0.80 (24 on time, 6 unpaid) |
| H1b, H1c, H3, H4, H5 | not testable (1 or 0 non-zero pairs) |
| H3 cargo (`waybill-deliveries`) | 7 cross-region needs, 28 monitors per game. `enforced` 0 at the horizon and at the end; `cheap_talk` 818 at the horizon and 1,425 at the end (company 0, cargo 8 to industry 6). Paired difference -818 (horizon), -1,425 (end): 1 non-zero pair, not testable |
| H4 market share (`waybill-deliveries`) | `enforced`: no market with a delivery (no value); `cheap_talk`: 1 market, exclusive (company 0 alone, 100%; it holds signed licences with companies 1 and 3). 0 pairs: not testable |
| H1c power (from this game) | SD of a company rate 0.23; minimum detectable difference with 80% power at 30 seeds about 0.18 |

The pipeline reports the missing power and does not crash.

## 6. Delivery extraction, and what the merged log cannot give

The merged log has only the deliveries of contracts. The H3 cargo measure and the H4 market-share measure need the deliveries per company, cargo and destination industry. `railhead waybill-deliveries` (`railhead.multi.deliveries`) gets them from a replay:

- It replays the merged log with `replay_multi` and the arguments of `verify_multi`. Then it runs the world to the horizon (the market settles at its forced stops, as the verifier does) and through the run-off.
- The cross-region needs are the `cross_only` needs of `cross_region_needs` within 128 tiles (`REACH_TILES`), from the industry snapshot at the start tick: the (destination industry, cargo) pairs with a producer within reach and none in the destination's own region. These are the needs that the seed rule of prereg 8 counts.
- At the start tick, before the market adds its first monitor (`on_start`), the pass adds an engine cargo monitor for each company and each need. It reads the monitors after each step (the `observe` hook), at the end of the log, at the horizon and at the end of the run-off. Industries that open later are not in the needs.
- The monitors are not in the checksum, but they share the GameScript's monitor list with the market. The GameScript gives a day's delivery on one (company, cargo, industry) key to the first monitor of that key, and the id of a monitor is its position in the list. A `MonitorMux` gives the market's monitor book the ids and totals that it gets in a plain replay. Tests: `tests/unit/test_waybill_deliveries.py` (a fake GameScript), and on the engine a collision with a real delivery (`test_waybill_market.py`) and an arena run (`test_waybill_cinema.py`).
- Each number that `result.json` also has (net worth, ledger, score, steps, contract totals) and the full market state of `verify/scores.json` must be equal. Otherwise the file has `check.ok: false`, the command exits with 1, and the analysis does not use the file.
- Cost: about 6 to 9 seconds per pilot v3 game (short track, 730 days, run-off 365 days).

What is still not available:

- The prereg does not say which window H3 cargo and H4 use. The analysis gives both (`end`, `horizon`). Section 5 shows `end` first because it is the window of the score.
- H2 uses a proxy (section 3) for "the seller had a route before the deadline".
