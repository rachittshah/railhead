# Water and rail in Railhead: results note

The briefing tells players that road, rail, air and water are all allowed.
This note checks whether rail and water really work through `rh`, what blocked models, what changed,
and what each mode earns on the game's own economics.

## Summary

- Both modes work with player primitives only. A ferry route and a coal train route, built by the new
  oracles through `World.act`, deliver and earn within a year on `arena-dev-0100`, and their action logs
  replay without divergence. The same logs also replay with no failed action on the unmodified
  GameScript, so the primitives already worked. What was missing was information and correct messages.
- Real games did not avoid rail. In 22 of 34 games the model built rail and in 20 a train ran. Rail never
  scaled: 15 of those 20 players ran one train. Nobody built a dock, a ship depot or a ship.
- On the game's economics, rail and air pay back at a similar rate per pound (see the table). Air earns
  far more per player action and per route. Water and road pay back about half as fast in 1960.
  So air is the rational choice under a call budget, and rail is not avoided for its economics.

## 1. What 34 real games did

Source: the action logs of 11 Arena 2 games (`arena-dev-0100`) and 23 seed games (`arena-train-02xx`).
Tool: `python -m railhead.oracle.census <actions.jsonl>...` (`src/railhead/oracle/census.py`). It assigns
each act to a mode by method name, and each vehicle to the mode of the depot it was bought in.

| Mode | Games that tried it | Games with a vehicle started | Calls (ok / failed) | Vehicles bought | Median calls before the first start |
|---|---:|---:|---:|---:|---:|
| air | 32 | 28 | 5,735 / 1,306 | 1,422 | 14 |
| road | 29 | 26 | 2,581 / 373 | 642 | 18 |
| rail | 22 | 20 | 1,739 / 217 | 104 trains, 233 wagons | 24.5 |
| water | 1 | 0 | 0 / 1 | 0 | - |

- Trains started per rail player: 1 (15 players), 2, 8, 11, 12 and 29.
- The one water attempt: `claude-fable-5-1` ran `water.dock dry_run=true` once and got `ERR_SITE_UNSUITABLE`.
  Five other players listed ship engines and went no further.
- Top rail failures: `ERR_AREA_NOT_CLEAR` 80, `ERR_TUNNEL_CANNOT_BUILD_ON_WATER` 50,
  `ERR_FLAT_LAND_REQUIRED` 42, `ERR_BAD_ARG` 15, `ERR_LAND_SLOPED_WRONG` 14. `rail.line` failed 122 of 431 times.
- `ERR_TUNNEL_CANNOT_BUILD_ON_WATER` reached 18 of 34 players 137 times, in air, rail and road builds.
  No tunnel was involved: the script API gives the engine's "can't build on water" error a tunnel name.

What the models said (from the 34 transcripts):

| Theme | Players | Example |
|---|---:|---|
| Calls: rail costs many, air few | 5 (all Claude Opus runs) | "an air route takes about 10 calls, versus dozens for rail"; "Coal rail would add maybe £200k/yr for ~35 calls — less per call than more air routes" |
| Train stuck, circling or not delivering | 5 | "cycling on the approach with no delivered cargo"; "deadlock after one delivery" |
| Station catchment missed the accepting tiles | 5 | "its catchment missed the tiles that accept coal" |
| Up-front capital for track | 4 | "without first funding hundreds of tiles of track" |
| Wrong or missing corner pieces | 3 | "I built one junction with the wrong track piece" |
| Misleading error text | 3 | "airport placement on water returned a tunnel error" |
| Depot reported `connected: false`, train stuck | 2 | "the depot reported `connected: false` ... the train never reached its first station" |
| Water seen only as an obstacle | about 5 | no model gave a reason to skip ships; none could see where a dock fits |

Profitable rail when it ran: "coal trains pay back after about two trips" (Opus, `arena-train-0203`);
"One train carrying 240 tons of coal earned about £14k per trip, so I moved to rail" (Sonnet).

## 2. Proof: end-to-end routes from primitives

`tests/integration/test_modes.py` loads `tasks/arena-dev-0100/start.sav` (1960, temperate, a large
inland sea), builds with an oracle, waits 365 days, and checks deliveries, route profit and replay.

| Route | Actions | Built | First 365 days | Replay |
|---|---:|---|---|---|
| Ferry, Trunningville to Grondham, 91 tiles of water | 13 | 2 docks, 1 ship depot, 2 MPS Passenger Ferries | 373 passengers delivered, ferries' profit £7,996 | valid, 0 divergences |
| Coal train, mine 59 to power station 61, 56 track tiles | 21 | 2 stations (1 x 3), 1 depot, 1 UU '37' + 4 coal trucks | 1,080 t delivered, train's profit £43,621 | valid, 0 divergences |

Baseline: the two action logs, replayed by `python -m railhead.verify` from the main checkout (the
unmodified GameScript at the same commit), give `valid 1`, `failed_acts 0`, `divergences 0`.
The rail oracle reads only queries and fields that existed before this work.

## 3. What was broken and what changed

| Finding (evidence) | Change | Where |
|---|---|---|
| No way to see where a dock fits: the only test was a dry run per tile | `dock` layer in `tiles` and `rh map layer=dock` (`d` marks a fit); `tile` gives `incline` and `dock {ok, water_tile, why}` | `lib/query.nut` `QH.DockCheck`, `H_tiles`, `H_tile`; `observe.py` |
| No overview of a 512 x 512 map: lakes need 64 map calls | `tiles step=N` samples every N-th tile; `rh map ... step=8` | `lib/query.nut` `H_tiles`; `observe.render_map` |
| `ERR_TUNNEL_CANNOT_BUILD_ON_WATER` for every build on water or shore (137 hits) | Outside `bridge.tunnel` the code is `ERR_CANNOT_BUILD_ON_WATER` with a hint about water and shore tiles | `lib/action.nut` `FailWith`, `HINTS` |
| `water.dock` failure said only "site unsuitable" | Hint names the reason for this tile (no incline, no flat water on the low side, tile occupied) | `lib/action.nut` `H_water_dock` |
| `water.ship_depot` doc said "oriented toward toward_tile"; the engine always builds on tile and tile +x or +y | Doc and failure hint describe the real footprint | `actions.py`, `lib/action.nut` |
| Dock catchment was not stated (it is 5) | `water.dock` doc, recipe, reply `radius` | `actions.py` |
| Heliport built with no hangar and no hint (`gpt-6-luna`, `gpt-5.6-luna`) | Reply `note` when the airport has no hangar | `lib/action.nut` `H_air_airport` |
| Depot `connected: false` left trains stuck | Reply `hint` when no track leads out of the depot front | `lib/action.nut` `H_rail_depot`, `DepotExits` |
| Catchment misses (5 players) | Station builds reply `radius`, `accepts` and `supplies` | `lib/action.nut` `Coverage` |
| Wrong corner pieces were invisible | `tile` shows `tracks` (piece names), `signals`, `depot_front` | `lib/query.nut` `H_tile`; `observe.format_tile` |
| `rh engines` showed `#0` for cargo and no refit list | Labels, `refit_cargos`, `carries`, `rail_type` columns | `lib/query.nut` `EngineRow`; `cli.py` |
| No route recipe; models spent dozens of exploratory calls | `rh help rail`, `rh help water` end with a minimal recipe; `rh help` and `rh brief` point to them | `cli.py` `RECIPES` |
| Ships cannot pass river rapids; nothing said so | Recipe and USAGE warn; the water oracle floods only flat water and locks | `cli.py`, `docs/USAGE.md`, `oracle/terrain.py` |

No existing command changes what it does to the world. The changes add replies, hints, queries and
query arguments, and rename one error code in replies. Replay compares only success or failure and
checksums, not codes.

## 4. Determinism

- `railhead recheck` on 44 certified tasks (10 arena, 10 deliver, 8 deliver_bid, 8 simops, 8 tycoon):
  `{"checked": 44, "mismatches": 0}`, run after the last GameScript change.
- Three Arena 2 logs re-verified with the final GameScript, chosen to cover the changed handlers:
  

  | Log | Why it was chosen | Acts (failed) | Net worth after year 11 | Reward before | Reward after |
  |---|---|---:|---:|---:|---:|
  | `claude-sonnet-5-5` | heavy rail, many errors under the renamed code | 1,021 (208) | £3,930,521 | 0.79293 | 0.79293 |
  | `claude-fable-5-1` | 29 trains, the one `water.dock` probe | 566 (21) | £9,837,139 | 1.0 | 1.0 |
  | `gpt-6-luna` | heliports, failed train, rail depots | 239 (54) | £236,701 | 0.23162 | 0.23162 |

  "Before" is each game's issued `verify/reward.json`. The full reward records after re-verification are
  identical field by field (`valid 1`, `divergences 0`). The certified oracle logs in `recheck` are truck
  routes, so the Arena logs are the evidence for the changed rail, air and dock handlers.

## 5. Economics

Tool: `python -m railhead.oracle.modes <start.sav> --seeds 1,2,3 --years 3` (`src/railhead/oracle/modes.py`).
Each run loads the start save, reseeds the hidden randomness, builds one simple route with an oracle
(player actions only), and steps to 1 January of the next three years. Build cost is the fall in
net worth (money minus loan) during the build. Yearly income is the route vehicles' `profit_last_year`
(fares minus running costs; station upkeep and loan interest are not in it), the mean of the two full
years after the first calendar year. Actions count every player action the oracle sent, dry runs included.
Raw rows: `docs/data/modes-econ-0100.json`, `docs/data/modes-econ-0204.json`.

`arena-dev-0100` (1960, inland sea), mean of 3 seeds:

| Mode | Route | Build cost | Actions | Yearly income (min-max) | Payback | Income per £ | Income per action |
|---|---|---:|---:|---:|---:|---:|---:|
| water | PASS, 91 tiles, 2 ferries | £37,671 | 13 | £22,175 (£19,863-£23,433) | 1.7 y | 0.59 | £1,706 |
| rail | COAL, 56 tiles, 1 train (4 wagons) | £31,680 | 21 | £46,999 (£46,082-£48,786) | 0.7 y | 1.48 | £2,238 |
| air | PASS, 205 tiles, 2 aircraft | £107,458 | 14 | £146,527 (£140,292-£152,008) | 0.7 y | 1.36 | £10,466 |
| road | COAL, 54 tiles, 10 trucks | £53,604 | 48 | £35,370 (£35,368-£35,371) | 1.5 y | 0.66 | £737 |

`arena-train-0204` (1990, no sea, a lake), mean of 3 seeds:

| Mode | Route | Build cost | Actions | Yearly income (min-max) | Payback | Income per £ | Income per action |
|---|---|---:|---:|---:|---:|---:|---:|
| water | PASS, 139 tiles, 2 ferries | £57,475 | 13 | £54,941 (£48,427-£65,080) | 1.0 y | 0.96 | £4,226 |
| rail | COAL, 56 tiles, 1 train (4 wagons) | £35,615 | 21 | £30,312 (£28,872-£33,126) | 1.2 y | 0.85 | £1,443 |
| air | PASS, 268 tiles, 2 aircraft | £160,936 | 14 | £129,884 (£127,786-£132,609) | 1.2 y | 0.81 | £9,277 |
| road | GRAI, 51 tiles, 8 trucks | £54,958 | 43 | £18,255 (£17,202-£19,666) | 3.0 y | 0.33 | £425 |

For comparison, the median calls before the first vehicle start in real games: air 14, road 18, rail 24.5.

Reading:

- Per pound invested, air does not win. In 1960 the coal train pays back as fast as the aircraft
  (0.7 years each) and returns slightly more per pound. In 1990 rail, air and water are within 20 % of each other.
- Per player action and per route, air wins by far: about 4.7 to 6.4 times rail's income per action,
  and 3.1 to 4.3 times its income per route. A model that optimises income per call (the Opus runs said so)
  chooses air correctly.
- Air also scales by buying more aircraft for the same two airports. The single-track rail route
  cannot take a second train without a passing loop and signals, and each new coal route costs about
  20 more actions. That is friction in the action space, not worse economics.
- Water in 1960 earns about half of rail per pound (slow 32 km/h ferries) but it is profitable. In
  1990 the ferry is the best of the four per pound.
- Consequence for the benchmark: on these maps the game's economics alone do not make air the only
  good play, but the call cost does. To measure transport strategy rather than "find the airline",
  tasks need either a budget in money rather than calls, or maps where air is weak (no large-airport
  sites, short distances, freight-only objectives) and rail or water clearly win.

Caveats: one route shape per mode, 2 vehicles (1 train); seeds vary hidden randomness, not the map,
so the build is the same in each seed. The road route is the existing road oracle, unchanged.

## 6. Open items

- Two trains on one line with signals and a passing loop: not built or tested. The recipe says that a
  second train needs them. A rail oracle for a double track or a loop is the next step.
- `rail.line` and `road.line` failures name only the start tile in the hint. An obstacle in the middle of
  a drag (most of the 80 rail `ERR_AREA_NOT_CLEAR`) is still not named. Left out to avoid one more
  GameScript change after the determinism runs.
- Water connectivity is not a query: the player sees it in the overview map and the `slope` layer.
  The water oracle checks it with a flood fill on its own side.
- On `arena-train-0204` the first ferry route lost money: the oracle's flood fill crossed river rapids,
  so the ferries never reached the river dock. The oracle now floods only flat water and locks, and the
  recipe warns players.
