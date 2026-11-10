# A/B report: pilot-water-rail

## Pre-registration (written before the run)

- Hypothesis: With water and rail transport fixed (treatment = this branch), the same Codex models on the same three 5-year maps put a larger share of their vehicle spend into non-air modes than with the current environment (control = the main branch in a sibling checkout). Direction: treatment - control > 0.
- Primary metric: `non_air_vehicle_spend_share` (1 - air vehicle spend / all vehicle spend (None when nothing was bought))
- Secondary metrics: `net_worth`, `score_k50`, `rail_route`, `water_route`, `failed_rate`
- Plan hash `86d3cb7687ac`; first run 2026-11-10T07:03:07+00:00; plan unchanged since then.
- Notes: Pilot, n = 3 tasks x 2 models x 1 replicate = 6 pairs, 12 games. Step 0 (before this plan): run the A/A check (docs/AB.md) and confirm a zero difference. Tasks: tracked 5-year (1825-day) dev tasks, identical task.json/start.sav/secret.json in both checkouts on 2026-11-10, briefing states the log-scale reward, quantity_sea_lakes = 2 (water is possible), positive scripted baseline, three start years (1975, 1960, 2000) so that the available aircraft differ. WHAT IT CAN SHOW: whether the harness, both arms and the metrics work end to end on real models; the direction and rough size of the change in mode mix for these 6 pairs; which water/rail failures (error codes, abandoned attempts) remain. WHAT IT CANNOT SHOW: a significant effect. With 3 tasks the smallest two-sided sign-test p is 0.25 and the task bootstrap has only 10 distinct resamples, so even a 3-of-3 result is not evidence. One replicate per cell cannot separate the arm effect from run-to-run variance of the same model on the same map. Seed-game logs show 68-99% of Codex vehicle spend in air on 10-year maps, so a ceiling/floor effect on the primary metric is likely. Use the pilot to size a confirmatory run (at least 6 tasks with a non-zero difference for p < 0.05, plus replicates). If the treatment arm regenerates task saves, add a task_map and expect a task-hash warning in the report.

Arms: control, treatment (baseline `control`; differences are arm - baseline). Billing: subscription. Run order seed: 20261110. Bootstrap: B=10000, seed 0, resampling tasks.

## treatment - control

n = 3 task(s) is too small to conclude anything: the smallest possible two-sided sign-test p is 0.25 (p < 0.05 needs at least 6 tasks with a non-zero difference), and the bootstrap over 3 task(s) has only 10 distinct resamples. Read the numbers as a description of these games, not as evidence of an effect.

| metric | n tasks | mean control | mean treatment | diff | 95% CI | sign p | tasks b>a / b<a | tied |
|---|---|---|---|---|---|---|---|---|
| **non_air_vehicle_spend_share** (primary) | 3 | 0.157 | 0.249 | 0.092 | [-0.017, 0.217] | 1.000 | 2 / 1 | yes |
| net_worth | 3 | 2.164M | 2.262M | 0.098M | [-0.336M, 0.357M] | 1.000 | 2 / 1 | yes |
| score_k50 | 3 | 0.687 | 0.701 | 0.015 | [-0.109, 0.109] | 1.000 | 2 / 1 | yes |
| rail_route | 3 | 1 | 0.833 | -0.167 | [-0.500, 0] | 1.000 | 0 / 1 | yes |
| water_route | 3 | 0.333 | 0.333 | 0 | [-0.500, 0.500] | 1.000 | 1 / 1 | yes |
| failed_rate | 3 | 0.245 | 0.223 | -0.023 | [-0.063, 0.031] | 1.000 | 1 / 2 | yes |
| score_k1000 | 3 | 0.391 | 0.407 | 0.015 | [-0.042, 0.062] | 1.000 | 2 / 1 | yes |
| net_worth_horizon | 3 | 1.504M | 1.515M | 0.011M | [-0.202M, 0.216M] | 1.000 | 2 / 1 | yes |
| vehicles_total | 3 | 39 | 48.8 | 9.833 | [-9.500, 24.5] | 1.000 | 2 / 1 | yes |
| vehicle_spend_total | 3 | 0.769M | 0.757M | -0.012M | [-0.218M, 0.157M] | 1.000 | 2 / 1 | yes |
| spend_total | 3 | 1.057M | 1.187M | 0.130M | [0.047M, 0.173M] | 0.250 | 3 / 0 | no |
| vehicles_road | 3 | 5.333 | 5 | -0.333 | [-2.500, 3.500] | 1.000 | 1 / 2 | yes |
| vehicles_rail | 3 | 14.8 | 27.8 | 13 | [1, 29] | 0.250 | 3 / 0 | no |
| vehicles_air | 3 | 18.3 | 15.5 | -2.833 | [-8, 1.500] | 1.000 | 1 / 2 | yes |
| vehicles_water | 3 | 0.500 | 0.500 | 0 | [-0.500, 0.500] | 1.000 | 1 / 1 | yes |
| vehicle_spend_share_road | 3 | 0.044 | 0.038 | -0.006 | [-0.029, 0.018] | 1.000 | 1 / 2 | yes |
| vehicle_spend_share_rail | 3 | 0.105 | 0.201 | 0.096 | [-0.002, 0.246] | 1.000 | 2 / 1 | yes |
| vehicle_spend_share_air | 3 | 0.843 | 0.751 | -0.091 | [-0.217, 0.017] | 1.000 | 1 / 2 | yes |
| vehicle_spend_share_water | 3 | 0.008 | 0.010 | 0.002 | [-0.006, 0.012] | 1.000 | 1 / 1 | yes |
| routes_road | 3 | 1.500 | 1.167 | -0.333 | [-1, 0] | 1.000 | 0 / 1 | yes |
| routes_rail | 3 | 2 | 4.167 | 2.167 | [0, 4] | 0.500 | 2 / 0 | yes |
| routes_air | 3 | 11.2 | 11.8 | 0.667 | [-2.500, 2.500] | 1.000 | 2 / 1 | yes |
| routes_water | 3 | 0.333 | 0.333 | 0 | [-0.500, 0.500] | 1.000 | 1 / 1 | yes |
| rail_tried | 3 | 1 | 0.833 | -0.167 | [-0.500, 0] | 1.000 | 0 / 1 | yes |
| water_tried | 3 | 0.333 | 0.333 | 0 | [-0.500, 0.500] | 1.000 | 1 / 1 | yes |
| rail_kept | 3 | 1 | 0.833 | -0.167 | [-0.500, 0] | 1.000 | 0 / 1 | yes |
| water_kept | 3 | 0.333 | 0.333 | 0 | [-0.500, 0.500] | 1.000 | 1 / 1 | yes |
| actions | 3 | 299.5 | 350.7 | 51.2 | [-77, 128] | 1.000 | 2 / 1 | yes |
| forks_created | 3 | 0.667 | 0.167 | -0.500 | [-1, 0] | 0.500 | 0 / 2 | yes |
| fork_days_used | 3 | 105.8 | 60.8 | -45 | [-182.5, 137.5] | 1.000 | 1 / 2 | yes |
| max_loan | 3 | 0.300M | 0.317M | 0.017M | [0.000M, 0.050M] | 1.000 | 1 / 0 | yes |
| final_loan | 3 | 0.000M | 0.000M | 0.000M | [0.000M, 0.000M] | 1.000 | 0 / 0 | yes |
| submitted | 3 | 1 | 1 | 0 | [0, 0] | 1.000 | 0 / 0 | yes |
| days_played | 3 | 1,825 | 1,825 | 0 | [0, 0] | 1.000 | 0 / 0 | yes |
| model_requests | 3 | 77.7 | 128.5 | 50.8 | [-9, 114] | 1.000 | 2 / 1 | yes |
| model_input_tokens | 3 | 5,678,048.2 | 11,344,873.5 | 5,666,825.3 | [-1,214,654, 13,320,510] | 1.000 | 2 / 1 | yes |
| model_output_tokens | 3 | 23,912.3 | 32,177.2 | 8,264.8 | [-3,284, 16,273.5] | 1.000 | 2 / 1 | yes |
| model_cost_usd | 3 | 0 | 0 | 0 | [0, 0] | 1.000 | 0 / 0 | yes |
| model_wall_clock_s | 3 | 1,091.8 | 1,496.8 | 405.0 | [89.8, 675.3] | 0.250 | 3 / 0 | no |

Primary metric per model (`non_air_vehicle_spend_share`):

| model | n tasks | mean a | mean b | diff | 95% CI | tied |
|---|---|---|---|---|---|---|
| gpt-6-astra | 3 | 0.212 | 0.250 | 0.038 | [-0.056, 0.201] | yes |
| gpt-6-sol | 3 | 0.103 | 0.248 | 0.145 | [-0.003, 0.233] | yes |

## Categorical behaviour (number of finished games)

| metric | arm | values |
|---|---|---|
| first_vehicle_mode | control | air: 6 |
| first_vehicle_mode | treatment | air: 4, rail: 2 |
| first_profitable_route_mode | control | air: 6 |
| first_profitable_route_mode | treatment | air: 4, rail: 2 |
| rail_status | control | route_kept: 6 |
| rail_status | treatment | not_tried: 1, route_kept: 5 |
| water_status | control | not_tried: 4, route_kept: 2 |
| water_status | treatment | not_tried: 4, route_kept: 2 |
| end | control | submitted: 6 |
| end | treatment | submitted: 6 |

## Cells (mean over replicates; verified and valid games only)

- control arena-dev-0005|gpt-6-astra: games 1, non_air_vehicle_spend_share 0.245, net worth 0.979M, score 0.455
- control arena-dev-0005|gpt-6-sol: games 1, non_air_vehicle_spend_share 0.152, net worth 0.311M, score 0.245
- control arena-dev-0008|gpt-6-astra: games 1, non_air_vehicle_spend_share 0.259, net worth 2.296M, score 0.815
- control arena-dev-0008|gpt-6-sol: games 1, non_air_vehicle_spend_share 0.121, net worth 1.162M, score 0.654
- control arena-dev-0014|gpt-6-astra: games 1, non_air_vehicle_spend_share 0.132, net worth 4.555M, score 1
- control arena-dev-0014|gpt-6-sol: games 1, non_air_vehicle_spend_share 0.035, net worth 3.684M, score 0.951
- treatment arena-dev-0005|gpt-6-astra: games 1, non_air_vehicle_spend_share 0.446, net worth 0.931M, score 0.444
- treatment arena-dev-0005|gpt-6-sol: games 1, non_air_vehicle_spend_share 0.386, net worth 1.072M, score 0.474
- treatment arena-dev-0008|gpt-6-astra: games 1, non_air_vehicle_spend_share 0.204, net worth 2.285M, score 0.814
- treatment arena-dev-0008|gpt-6-sol: games 1, non_air_vehicle_spend_share 0.326, net worth 1.716M, score 0.745
- treatment arena-dev-0014|gpt-6-astra: games 1, non_air_vehicle_spend_share 0.101, net worth 6.073M, score 1
- treatment arena-dev-0014|gpt-6-sol: games 1, non_air_vehicle_spend_share 0.032, net worth 1.496M, score 0.732

## Provenance

- control: commit 012f1a7473, dirty True, diff ae59b3134afd, engine 35252e23ba63, prompt 90dc8933363c
- treatment: commit 6553bc9f09, dirty False, diff e3b0c44298fc, engine 35252e23ba63, prompt 90dc8933363c
- harness versions: {'codex': ['codex-cli 0.165.0']}

## Metric definitions

- `non_air_vehicle_spend_share`: 1 - air vehicle spend / all vehicle spend (None when nothing was bought)
- `net_worth`: verified net worth (cash - loan) after the run-off
- `score_k50`: arena score: tycoon_log(net worth, w_idle, w_base, k=50), the task reward
- `rail_route`: 1 if a started rail vehicle ever had >= 2 distinct order tiles
- `water_route`: 1 if a started water vehicle ever had >= 2 distinct order tiles
- `failed_rate`: act records with ok=false / all act records
- `score_k1000`: tycoon_log with k=1000 (separates strong games)
- `net_worth_horizon`: verified net worth at the end of the mandate
- `vehicles_total`: vehicles bought or cloned, all modes
- `vehicle_spend_total`: money paid for vehicles, all modes
- `spend_total`: vehicle plus construction spend, all modes and 'other'
- `vehicles_road`: vehicles bought or cloned, road
- `vehicles_rail`: vehicles bought or cloned, rail
- `vehicles_air`: vehicles bought or cloned, air
- `vehicles_water`: vehicles bought or cloned, water
- `vehicle_spend_share_road`: road vehicle spend / all vehicle spend
- `vehicle_spend_share_rail`: rail vehicle spend / all vehicle spend
- `vehicle_spend_share_air`: air vehicle spend / all vehicle spend
- `vehicle_spend_share_water`: water vehicle spend / all vehicle spend
- `routes_road`: distinct road stop-tile sets of vehicles alive at the end
- `routes_rail`: distinct rail stop-tile sets of vehicles alive at the end
- `routes_air`: distinct air stop-tile sets of vehicles alive at the end
- `routes_water`: distinct water stop-tile sets of vehicles alive at the end
- `rail_tried`: 1 if any rail action or rail vehicle buy was attempted
- `water_tried`: 1 if any water action or water vehicle buy was attempted
- `rail_kept`: 1 if such a rail vehicle is alive at the end
- `water_kept`: 1 if such a water vehicle is alive at the end
- `actions`: act records in the log (dry runs included), as the verifier counts them
- `forks_created`: fork_create results ({"fork": ..., "exact": ...}) in the tool output of the transcript (forks are not in the action log; None without a transcript)
- `fork_days_used`: max_fork_days of the task - the lowest fork_days_left printed in the transcript
- `max_loan`: highest loan in the loan path
- `final_loan`: loan at the end of the log
- `submitted`: 1 if the player ran rh submit
- `days_played`: game days from the start to the last checksum in the log
- `model_requests`: LLM requests (see 'model')
- `model_input_tokens`: input tokens
- `model_output_tokens`: output tokens
- `model_cost_usd`: cost reported by the harness (API price)
- `model_wall_clock_s`: wall-clock seconds of the cell
