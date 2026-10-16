# 03 — OpenTTD 15.2 engine internals for Railhead (source-verified)

**Source:** `git clone --depth 1 --branch 15.2 https://github.com/OpenTTD/OpenTTD.git` → commit `c91185ebd95f4bd8356465ee8a732aa058beb745` (2026-02-18).
**Method:** every claim below cites `path:line` relative to the repo root at that commit. Anything not read in code is marked **UNVERIFIED**. Nothing was built or measured.
**Note:** tag `15.3` (2026-04-04) exists; it fixes a desync (see §4.6). Spot-checked: `GSAdmin.Send` is byte-identical in 15.3 and `master`.

---

## 0. Key verified facts (TL;DR)

| Question | Answer | Evidence |
|---|---|---|
| Admin protocol version | `3` | `src/network/core/config.h:48` |
| Byte order / strings | little-endian ints; strings are raw bytes + `\0`, no length prefix | `src/network/core/packet.h:35-38`, `packet.cpp:172-177` |
| Packet framing | `uint16 size (LE, includes itself + type)` · `uint8 type` · payload | `packet.cpp:66-72`, `packet.cpp:259-272` |
| Inbound packet cap (admin→server) | 32767 B (`TCP_MTU`) | `tcp.cpp:126`, `config.h:45` |
| Outbound packet cap (server→admin) | 1460 B (`COMPAT_MTU`, default `Packet` limit) | `packet.h:59`, `config.h:46` |
| **Admin→GS JSON max** | **8999 bytes** (`NETWORK_GAMESCRIPT_JSON_LENGTH`=9000 incl. `\0`); longer is silently truncated → parse fails | `config.h:60`, `network_admin.cpp:505`, `packet.cpp:425-441` |
| **GS→Admin JSON max** | **1456 bytes** plaintext (1460−2−1−1); **1440** if the admin session is encrypted (16-byte MAC). **No length check exists in 15.2**; overflow trips an `assert` (server FatalError on assert-enabled builds) or emits an oversized packet on stable builds | `script_admin.cpp:119-137`, `network_admin.cpp:550-558`, `packet.cpp:48-60,172-177`, `network_crypto_internal.h:20` |
| Plain-password `ADMIN_JOIN` | Works only with `network.allow_insecure_admin_login=true` (default **false**) and non-empty `admin_password` | `network_admin.cpp:629-639`, `network_settings.ini:181-185` |
| **GS can act as a company** | Yes. `GSCompanyMode(cid)` switches company; all rail/road/station/depot/bridge/tunnel/dock/airport build, vehicle buy/sell/refit/start, orders, loan and terraform calls are `EnforceCompanyModeValid` (company mode **required**) | §2.1 |
| **Fast-forward on dedicated** | **Not possible stock.** `_game_speed` is only changed by GUI code that is disabled when networking; no console command; reset to 100 on every new game/load. Dedicated server runs fixed 27 ms/tick. ~15-line patch needed | §3 |
| `-v null` as server | **No.** Null driver never sets `_is_network_server`, never starts networking/admin port | `null_v.cpp:49-64`, `dedicated_v.cpp:204-205`, `openttd.cpp:1031-1044` |
| **Determinism** | Yes, lockstep-grade: all game-state RNG is `_random` (seeded from generation seed), commands execute at fixed frames; script RNG seeded from `_random` and saved. Caveats: wall-clock link-graph pauses, `GSDate.GetSystemTime`, admin-message arrival tick, cross-binary differences | §4 |
| **Fork via savegame** | Yes. `save` (RCON) snapshots state synchronously; `-g file.sav` / `load` in another instance resumes. On load the GS/AI VMs restart (only `Save()` data survives), queued commands are dropped | §4.3-4.5 |
| Replay format | `SERVER_CMD_LOGGING` = (client_id, company, cmd_id, arg-buffer, exec frame). Native replay exists only behind compile flag `DEBUG_DUMP_COMMANDS` reading `commands.log` (from `-d desync=1` output `commands-out.log`) | §5 |
| Seed | `-G <seed>` or console `newgame <seed>`; **cannot** be set in `openttd.cfg` (`NotInConfig`) | `openttd.cpp:637-643`, `world_settings.ini:200-208` |
| **Human-parity command coverage via GS** | Of 145 commands, 100 are player-facing: **68 directly exposed, 11 reachable by other calls, 21 not reachable** (timetables, service interval, road waypoints, engine previews, mergers, …) | §7 |
| **Map image on dedicated** | **Yes** for `screenshot minimap` / `screenshot heightmap` (runtime-verified on 15.3, null blitter). **No** for `giant`/`normal`/`viewport` ("Screenshot failed!"); dedicated-only builds compile no 8/32 bpp blitters at all | §8, §13 |
| **15.3 patches** | `gamespeed` (sticky on dedicated), 1 ms paused frames, GS→admin JSON ≤32,747 B with a clean refusal above. All pass `git apply --check` on tag 15.3, build cleanly, and pass the project's engine-patch tests | §12, `assets/patches/` |

---

## 1. Admin protocol (`src/network/core/tcp_admin.*`, `src/network/network_admin.cpp`)

### 1.1 Framing, encoding, connection lifecycle
- Integers >1 byte are little-endian; `bool` is a `uint8` (0/1); strings are `\0`-terminated with no length — `packet.h:35-38`, `packet.cpp:98-114,172-177`.
- `Send_buffer` = `uint16 length` + raw bytes — `packet.cpp:183-188`.
- Size header: first 2 bytes, LE, equals total packet length including the size bytes and type byte — `packet.cpp:71-72`; receiver rejects `size < 3 || size > limit` — `packet.cpp:267`.
- Server receive limit for any TCP socket is `TCP_MTU`=32767 — `tcp.cpp:126`. Server-created packets default to `COMPAT_MTU`=1460 — `packet.h:59`.
- Inbound strings are sanitized by `StrMakeValid(..., ReplaceWithQuestionMark)` — `packet.h:90`, `packet.cpp:440`. Non-printables incl. `\n`, `\r`, `\t` become `?` — `string.cpp` `StrMakeValid` (printable/AllowNewline branches). **Consequence:** pretty-printed JSON sent to the GS is corrupted. Send compact JSON only.
- Default admin port 3977 — `config.h:26`; setting `network.server_admin_port` — `network_settings.ini:167-173`.
- Listener starts only if `admin_password` or `admin_authorized_keys` is configured — `network.cpp:907-911`, `settings_type.h:374`.
- Must authenticate within 10 s or be dropped — `network_admin.cpp:44,100-103`.
- Admin connections survive `newgame`/`load` on a **dedicated** server; they receive `SERVER_NEWGAME` then `SERVER_WELCOME` — `network.cpp:976-993` (`NetworkClose(!_network_dedicated)`), `network.cpp:970`.
- Per tick, admin sockets are read at the start of `NetworkGameLoop` and flushed at its end — `network.cpp:1050-1061` (receive), `network.cpp:1281` (send).

### 1.2 Authentication
| Path | Packets | Conditions | Evidence |
|---|---|---|---|
| Insecure | `ADMIN_JOIN{string password, string name, string version}` → `SERVER_PROTOCOL` + `SERVER_WELCOME` | `allow_insecure_admin_login=true` (default false) **and** password matches non-empty `admin_password`. Else `SERVER_ERROR(NOT_AUTHORIZED=6 / WRONG_PASSWORD=10)` | `network_admin.cpp:625-652`, `network_settings.ini:181-185` |
| Secure | `ADMIN_JOIN_SECURE{string name, string version, uint16 method_mask}` → `SERVER_AUTH_REQUEST{uint8 method, 32B server pubkey, 24B nonce}` → `ADMIN_AUTH_RESPONSE{32B client pubkey, 16B MAC, 8B encrypted}` → `SERVER_ENABLE_ENCRYPTION{24B nonce}` → everything after is encrypted | Method bits: `X25519_KeyExchangeOnly`=bit0 (always stripped for admin), `X25519_PAKE`=bit1 (password), `X25519_AuthorizedKey`=bit2 | `tcp_admin.h:207-231,505-522`, `network_admin.cpp:795-869`, `network_crypto.h:178-180` |

Secure path uses X25519 + Monocypher AEAD; a Python implementation must match Monocypher's key derivation and framing — **UNVERIFIED** which Python library is wire-compatible. Recommendation: insecure JOIN on `127.0.0.1` only.

Error codes (`uint8`, append-only) — `network_type.h:99-128`: GENERAL 0, DESYNC 1, SAVEGAME_FAILED 2, CONNECTION_LOST 3, ILLEGAL_PACKET 4, NEWGRF_MISMATCH 5, NOT_AUTHORIZED 6, NOT_EXPECTED 7, WRONG_REVISION 8, NAME_IN_USE 9, WRONG_PASSWORD 10, … NO_AUTHENTICATION_METHOD_AVAILABLE 22.

### 1.3 Packet types and payloads (server-side code is ground truth)
IDs from `tcp_admin.h:23-66`.

| ID | Packet | Payload (in order) | Evidence |
|---|---|---|---|
| 0 | ADMIN_JOIN | str password, str name (≤24), str version (≤32) | `network_admin.cpp:634-642` |
| 1 | ADMIN_QUIT | — | `:654-658` |
| 2 | ADMIN_UPDATE_FREQUENCY | **uint16** update_type, uint16 freq bitmask (must be subset of allowed, else disconnect ILLEGAL_PACKET) | `:660-678` |
| 3 | ADMIN_POLL | **uint8** update_type, uint32 id (`0xFFFFFFFF`=all for CLIENT/COMPANY_INFO) | `:680-744` |
| 5 | ADMIN_RCON | str command (≤499 B) | `:487-499`, `config.h:59` |
| 6 | ADMIN_GAMESCRIPT | str json (≤8999 B) | `:501-511` |
| 7 | ADMIN_PING | uint32 token | `:513-522` |
| 9 | ADMIN_JOIN_SECURE | str name, str version, uint16 method_mask | `:795-818` |
| 10 | ADMIN_AUTH_RESPONSE | 32B pubkey, 16B MAC, 8B ciphertext | `tcp_admin.h:221-231` |
| 102 | SERVER_ERROR | uint8 NetworkErrorCode (then connection closed) | `network_admin.cpp:130-145` |
| 103 | SERVER_PROTOCOL | uint8 version(=3); repeat {bool 1, uint16 update_type, uint16 allowed_freq_mask}; bool 0 | `:148-167` |
| 104 | SERVER_WELCOME | str server_name, str revision, bool dedicated, str "" (old map name), uint32 generation_seed, uint8 landscape, uint32 start date (1 Jan of starting_year, calendar days), uint16 map_x, uint16 map_y | `:170-188` |
| 105/106 | SERVER_NEWGAME / SHUTDOWN | — | `:191-204` |
| 107 | SERVER_DATE | uint32 **calendar** date (days) | `:207-215` |
| 113 | SERVER_COMPANY_NEW | uint8 company | `:307-315` |
| 114 | SERVER_COMPANY_INFO | uint8 id, str name, str manager, uint8 colour, bool (always true, legacy "protected"), uint32 inaugurated_year, bool is_ai, **uint8 quarters_of_bankruptcy** (field missing from header doc) | `:321-337` |
| 115 | SERVER_COMPANY_UPDATE | uint8 id, str name, str manager, uint8 colour, bool true, uint8 quarters_of_bankruptcy (header doc's 4 share owners are no longer sent) | `:344-358` |
| 116 | SERVER_COMPANY_REMOVE | uint8 id, uint8 reason (0 manual, 1 autoclean, 2 bankrupt) | `:365-375`, `tcp_admin.h:107-113` |
| 117 | SERVER_COMPANY_ECONOMY | uint8 id, uint64 money, uint64 loan, int64 income (current year, = −Σ yearly_expenses[0]), uint16 delivered_cargo this quarter (clamped), then ×2 {uint64 company_value, uint16 performance, uint16 delivered_cargo} for last & previous quarter | `:378-406` |
| 118 | SERVER_COMPANY_STATS | uint8 id, 5×uint16 vehicle counts (train, lorry, bus, plane, ship), 5×uint16 station counts | `:409-433`, `tcp_admin.h:418-434` |
| 120 | SERVER_RCON | uint16 colour, str line | `:476-485` |
| 121 | SERVER_CONSOLE | str origin, str text (dropped if >1460) | `:529-544` |
| 122 | SERVER_CMD_NAMES | repeat {bool 1, uint16 cmd_id, str name}; bool 0; may span several packets | `:572-599` |
| 124 | SERVER_GAMESCRIPT | str json | `:550-558` |
| 125 | SERVER_RCON_END | str original command | `:461-469` |
| 126 | SERVER_PONG | uint32 token | `:561-569` |
| 127 | SERVER_CMD_LOGGING | uint32 client_id, uint8 company, uint16 cmd_id, buffer(uint16 len + bytes) args, uint32 frame | `:606-619` |
| 128/129 | SERVER_AUTH_REQUEST / ENABLE_ENCRYPTION | see §1.2 | `:820-843` |

Also present but less relevant: CHAT(4/119), EXTERNAL_CHAT(8), CLIENT_*(108-112), FULL(100), BANNED(101), CMD_LOGGING_OLD(123, unused).

### 1.4 Update types and frequencies
`AdminUpdateType` — `tcp_admin.h:80-92`: DATE 0, CLIENT_INFO 1, COMPANY_INFO 2, COMPANY_ECONOMY 3, COMPANY_STATS 4, CHAT 5, CONSOLE 6, CMD_NAMES 7, CMD_LOGGING 8, GAMESCRIPT 9.
Frequency bitmask (bit index = enum value, `base_bitset_type.hpp:53`): Poll 0x01, Daily 0x02, Weekly 0x04, Monthly 0x08, Quarterly 0x10, Annually 0x20, Automatic 0x40 — `tcp_admin.h:95-103`.
Allowed combos — `network_admin.cpp:48-59`. **Railhead must register `GAMESCRIPT=9 @ 0x40`** or GS output is never delivered (`network_admin.cpp:1022-1029`). CMD_LOGGING=8 @ 0x40. Periodic DATE/ECONOMY/STATS updates fire on **economy** timers — `network_server.cpp:1874-1904`.

### 1.5 RCON semantics
- Executed synchronously inside packet handling: `IConsoleCmdExec` then `SERVER_RCON_END` — `network_admin.cpp:495-498`. Output lines arrive as `SERVER_RCON` before `RCON_END`.
- Commands that `Post` game commands (`pause`, `start_ai`) only enqueue; effect lands ≥1 frame later (§2.2).
- `save` returns before the file is written unless `gui.threaded_saves=false` (§4.3).

---

## 2. GameScript API (15.2, `src/script/api/`)

### 2.1 Can a GS act as a company? — capability matrix
`GSCompanyMode(cid)` sets `ScriptObject::SetCompany`; restored on destruction — `script_companymode.cpp:16-27`. All commands go through `DoCommandPrep`, which fails with `ERR_PRECONDITION_INVALID_COMPANY` unless deity or a valid company — `script_object.cpp:277-280`. Macros — `script_error.hpp:54-86`.

| Action | GS API | Mode required | Evidence |
|---|---|---|---|
| Rail track / rail line | `GSRail.BuildRailTrack`, `BuildRail` | Company | `script_rail.cpp:247,353` |
| Rail station / depot / signal | `BuildRailStation`, `BuildRailDepot`, `BuildSignal` | Company | `script_rail.cpp:153,139,444` |
| Road | `GSRoad.BuildRoad` | Deity **or** company | `script_road.cpp:522` |
| One-way road, road depot, road stations | `BuildOneWayRoad`, `BuildRoadDepot`, `BuildRoadStation`, `BuildDriveThroughRoadStation` | Company | `script_road.cpp:541,558,572` |
| Bridge | `GSBridge.BuildBridge` | Deity or company | `script_bridge.cpp:76` |
| Tunnel | `GSTunnel.BuildTunnel` | Deity or company; **rail tunnels need company** | `script_tunnel.cpp:84-89` |
| Dock / airport | `GSMarine.BuildDock`, `GSAirport.BuildAirport` | Company | `script_marine.cpp:91`, `script_airport.cpp:75` |
| Buy vehicle (+refit) | `GSVehicle.BuildVehicle`, `BuildVehicleWithRefit`, `CloneVehicle` | Company; also blocked if `ai.ai_disable_veh_<type>` is true | `script_vehicle.cpp:77-83,113`, `script_gamesettings.cpp:49-58` |
| Sell / refit / start-stop | `SellVehicle`, `RefitVehicle`, `StartStopVehicle` | Company | `script_vehicle.cpp:171,162,230` |
| Orders | `GSOrder.AppendOrder`, `InsertOrder`, `ShareOrders` | Company | `script_order.cpp:457,478,682` |
| Loan | `GSCompany.SetLoanAmount`, `SetMinimumLoanAmount` | Company | `script_company.cpp:254,273` |
| Terraform | `GSTile.RaiseTile`, `LowerTile`, `LevelTiles` | Company | `script_tile.cpp:262,270,278` |
| Demolish | `GSTile.DemolishTile` | Deity or company | `script_tile.cpp:287` |
| Inject money (reward shaping / cheats) | `GSCompany.ChangeBankBalance` | **Deity only** | `script_company.cpp:288` |

All these classes are `@api ai game`; no method-level `@api -game` exclusions exist in rail/road/vehicle/order/company/tile headers (only internal `@api -all` helpers) — grep over `src/script/api/*.hpp`. Costs are charged to the company; insufficient funds fail like a player — `script_companymode.hpp:20-24`.

**Landscaping is rate-limited per company** (terraform/clear/tree burst 4096, objects 2048, refilled every frame incl. paused frames) — `world_settings.ini:379-452`, `company_cmd.cpp:342-350`, `openttd.cpp:1226`.

**A GS cannot create a company.** No script API issues `CMD_COMPANY_CTRL`. Create the agent's company with RCON `start_ai <IdleAIName>` (`CCA_NEW_AI`) — `console_cmds.cpp:1455-1516`; requires `ai.ai_in_multiplayer=true` on a network server — `console_cmds.cpp:1467-1471`. If that AI's script dies, the company persists — `ai_instance.cpp:61-82`. AI companies are exempt from autoclean — `network_server.cpp:1578`.

### 2.2 Command execution model
- **Synchronous (default) in network mode:** command is test-run immediately (failure returns at once, no tick spent), then queued via `NetworkSendCommand`; the script suspends with `-delay` and waits for the callback `CcGame → Continue()` — `script_object.hpp:398-430`, `script_object.cpp:285-337` (esp. 323-325), `command_func.h:378-391`, `game_instance.cpp:95-100`, `script_instance.cpp:166-170,198-200`.
- Server queues own/script commands with `frame = _frame_counter_max + 1` and executes them in `NetworkExecuteLocalCommandQueue` **before** the next `StateGameLoop` — `network_command.cpp:223,304`, `network.cpp:1239-1255`. With `frame_freq=0` (default, `network_settings.ini:58-63`) a sync GS command executes on the next frame → **≤1 synchronous GS command per tick**. `GSController.SetCommandDelay(n)` (default 1, `script_storage.hpp:53`) only adds delay.
- **`GSAsyncMode(true)`** (game-only): command queued without waiting; script charged 100 ops; return value comes from the *test* run, so returned IDs (e.g. new `VehicleID`) are not meaningful — `script_asyncmode.hpp:15-24`, `script_object.cpp:309-321`. Server distributes at most `max(commands_per_frame, commands_per_frame_server)` per frame (defaults 2 / 16, max 65535); excess carries over — `network_command.cpp:331-337`, `network_settings.ini:67-81`.
- **`GSTestMode`**: estimate only, never networked, cost accumulates (`GSAccounting`) — `script_testmode.hpp:23-31`, `script_object.cpp:270,300-303`.
- **Pause interaction:** commands not permitted at `construction.command_pause_level` stay queued while paused — `network_command.cpp:342-345`, `command.cpp:144-162`. Default level 1 (`world_settings.ini:366-372`) blocks construction, vehicle building and money commands during pause. **Set `command_pause_level=3` (AllActions)** or a sync GS command issued while paused blocks the GS until unpause. Executing a non-ServerSetting command while paused sets `PauseMode::CommandDuringPause`, which clears automatically when the last real pause reason is removed — `command.cpp:387`, `misc_cmd.cpp:198-203`.
- `GSGame.Pause()/Unpause()` issue `CMD_PAUSE(PauseMode::GameScript)` — `script_game.cpp:20-28`; `CMD_PAUSE` is `ServerSetting` type so it is always allowed while paused — `misc_cmd.h:35`. Pause modes — `openttd.h:68-77`.
- **The GS keeps running while the game is paused**: `StateGameLoop` calls `Game::GameLoop()` in the paused branch — `openttd.cpp:1217-1230`. `GSController.GetTick()` therefore counts paused frames too — `script_instance.cpp:196`, `script_controller.cpp:77-80`.
- `Start()` must never return (return ⇒ script dies) — `script_instance.cpp:242`. No DoCommands in constructor/`Save()`/`Load()`/valuators — `script_object.cpp:265-267`.

### 2.3 Budgets
| Setting | Default | Range | Notes | Evidence |
|---|---|---|---|---|
| `script.script_max_opcode_till_suspend` | 10000 ops/tick | 500–250000 | NewgameOnly | `script_settings.ini:46-52`, `script_instance.cpp:242,262` |
| `script.script_max_memory_megabytes` | 1024 MiB | 8–8192 | NewgameOnly; exceeding → script fatal error | `script_settings.ini:60-66`, `squirrel.cpp:107-109,157` |
| `Save()` | 100000 ops, nesting ≤25 | — | — | `script_info.hpp:19`, `script_instance.hpp:22` |
| Async command | 100 ops each | — | — | `script_object.cpp:314` |

Budgets are opcode counts, not wall time.

### 2.4 GSAdmin.Send / GSEventAdminPort
- `GSAdmin.Send(table)`: top-level must be a table; converts int/string/array/table/bool/null via nlohmann JSON; **floats and class instances are rejected** (logs error, returns false, sends nothing); nesting ≤25 — `script_admin.cpp:31-137`. Serialized with `json.dump()` (compact) — `script_admin.cpp:133`.
- **No size guard.** Header doc still claims "larger than 1450 bytes → false" (`script_admin.hpp:33-34`) but code has no check (also in 14.1, 15.3, master). The packet is built with the 1460-byte default limit and `Send_string` only `assert`s — `network_admin.cpp:550-558`, `packet.cpp:172-177`. Asserts are compiled in for Debug builds and builds with `OPTION_USE_ASSERTS` (nightlies/betas/RCs), causing `FatalError` — `cmake/Options.cmake:59,104-108`, `stdafx.h:291-294`, `error.cpp:19-22`. Stable release builds define `NDEBUG`, so the oversized packet is sent; above 65535 the 16-bit size wraps and corrupts the stream — `packet.cpp:71-72`. **Budget ≤1400 bytes per message and chunk.**
- Inbound: `ADMIN_GAMESCRIPT` → `Game::NewEvent(new ScriptEventAdminPort(json))` — `network_admin.cpp:509`. GS reads `GSEventController.GetNextEvent()` → `GSEventAdminPort.Convert(e).GetObject()`. Root must be an object; floats rejected; any failure returns `null` — `script_event_types.cpp:129-209`. Integers become 64-bit `SQInteger` — `squirrel.h:35`.
- Events are **dropped silently** when no GS instance exists (e.g., during load before `Game::StartNew`) — `game_core.cpp:146-148`. No explicit queue cap found in `script_event.cpp`.

### 2.5 Observation / reward APIs (all `@api ai game` unless noted)
- `GSTile`: GetSlope, GetMinHeight/GetMaxHeight/GetCornerHeight, GetOwner, IsBuildable, IsBuildableRectangle, GetTerrainType, IsWaterTile/IsSeaTile/IsCoastTile, GetCargoAcceptance, GetCargoProduction, GetTownAuthority, GetBuildCost, HasTransportType, IsStationTile — `script_tile.hpp` (function list).
- `GSMap`: GetMapSize/X/Y, GetTileIndex, GetTileX/Y, Distance* — `script_map.hpp`.
- `GSIndustry`: GetLastMonthProduction, GetLastMonthTransported(+Percentage), IsCargoAccepted, GetStockpiledCargo, GetProductionLevel/SetProductionLevel, GetLocation, GetAmountOfStationsAround — `script_industry.hpp`.
- `GSStation`: GetCargoWaiting(+From/Via), GetCargoRating, GetCargoPlanned*, coverage radius — `script_station.hpp`.
- `GSStationList`, `GSVehicleList`, `GSDepotList`: deity sees all; company mode filters to that owner — `script_stationlist.cpp:18-28`, `script_vehiclelist.cpp:24-35`.
- `GSCompany`: GetBankBalance, GetLoanAmount, GetMaxLoanAmount, GetLoanInterval, GetQuarterlyIncome/Expenses/CargoDelivered/PerformanceRating/CompanyValue (quarter 0=current … EARLIEST_QUARTER) — `script_company.cpp:136-206`.
- `GSInfrastructure`: GetRailPieceCount, GetRoadPieceCount, GetInfrastructurePieceCount, GetMonthly*Costs — `script_infrastructure.hpp`.
- **`GSCargoMonitor` (game-only)**: Get{Town,Industry}{Delivery,Pickup}Amount(company, cargo, target, keep_monitoring) returns the amount since the previous call and resets; pickup is credited only on delivery; monitors are saved/loaded and stop on bankruptcy/merger; StopAllMonitoring — `script_cargomonitor.hpp:20-92`, `script_cargomonitor.cpp`. Well suited to verifiable per-route rewards.
- `GSDate.GetCurrentDate()` is the **economy** date — `script_date.cpp:23-26`. Calendar and economy diverge only in wallclock timekeeping mode (`economy.timekeeping_units`, default calendar) — `economy_settings.ini:311-329`.

---

## 3. Simulation speed

### 3.1 Stock behaviour
- One tick = `MILLISECONDS_PER_TICK` = **27 ms** (≈37 ticks/s); one day = 74 ticks ≈ 2.0 s — `gfx_type.h:370`, `timer_game_tick.h:75-76`.
- Pacing: `GetGameInterval()` = 27 ms × 100 / `_game_speed`; 0 means unlimited; **paused ⇒ always 27 ms** — `video_driver.hpp:298-314`.
- Dedicated loop: read stdin, drain queue, `Tick()`, `SleepTillNextTick()` — `dedicated_v.cpp:194-224`, `video_driver.cpp:104-115,173-185`.
- `_game_speed` writers: `ChangeGameSpeed` (`gfx.cpp:1858-1865`) called only from the GUI toolbar, which returns early if `_networking` (`toolbar_gui.cpp:222-230`), and from the fast-forward key path gated by `!_networking` (`video_driver.cpp:141`). It is reset to 100 in `InitializeGame` on every new game and load — `misc.cpp:101`. `gui.fast_forward_speed_limit` (default 2500 %) is only read by `ChangeGameSpeed` — `gui_settings.ini:876-887`.
- No console command touches game speed (full command list from `console_cmds.cpp` registrations).
- **Conclusion:** a stock `-D` server always runs at 1×.

### 3.2 Null driver
`-v null:ticks=N` runs `GameLoop()` N times with no sleep, optional exit-save, then exits — `null_v.cpp:33,49-64`. It never sets `_is_network_server` (only `dedicated_v.cpp:204-205` and GUI code do), so `NetworkServerStart` is never called (`openttd.cpp:1031-1044`) and there is no admin port. It is usable only for offline batch rollouts where the GS itself is the policy.

### 3.3 Minimal patch (recommended)
> Superseded by the verified 15.3 diffs in §12 and `assets/patches/`. The sketch below is kept for context.

1. New console command, ~15 lines in `src/console_cmds.cpp` (register next to `pause`, `console_cmds.cpp:2977`; needs `#include "gfx_func.h"`):
```cpp
static bool ConGameSpeed(std::span<std::string_view> argv)
{
	if (argv.empty()) {
		IConsolePrint(CC_HELP, "Set game speed in percent of normal, 0 = unlimited. Usage: 'gamespeed [<percent>]'.");
		return true;
	}
	if (argv.size() == 1) { IConsolePrint(CC_DEFAULT, "Game speed: {}", _game_speed); return true; }
	auto v = ParseInteger<uint16_t>(argv[1]);
	if (!v.has_value()) return false;
	_game_speed = *v;
	return true;
}
/* IConsole::CmdRegister("gamespeed", ConGameSpeed, ConHookServerOrNoNetwork); */
```
   Re-send `gamespeed 0` over RCON after every `SERVER_NEWGAME`/`WELCOME`, because `misc.cpp:101` resets it. Alternatively make the reset conditional.
2. Optional, for low-latency lockstep: in `video_driver.hpp:309` return `1 ms` instead of 27 ms when `_game_speed == 0 && _pause_mode.Any()`. Otherwise every frame spent while paused costs 27 ms, including each synchronous GS command executed during pause.

With `_game_speed=0`, `GameLoop` adds 0 to `next_game_tick` and the drift clamp resets it to `now`, so `SleepTillNextTick` never sleeps — `video_driver.cpp:30-37,173-185`. Wall-clock timers still fire (autosave, server restart) — `openttd.cpp:1289`, `network_server.cpp:1832`; disable them with `gui.autosave_interval=0` and keep the defaults `network.restart_hours=0` and `network.restart_game_year=0` (`network_settings.ini:240-252`).

Without patching, the only lever is server-side pausing for lockstep at 1× real time.

---

## 4. Determinism, save/load, forks

### 4.1 RNG
- `_random` drives game state; `_interactive_random` is "where it does not influence game state" — `random_func.hpp:43`. Non-GUI users of `InteractiveRandom`: seed choice when none given (`openttd.cpp:606`, `genworld.cpp:328`), network tokens (`network_server.cpp:630`), scenario-editor tree tool (`tree_cmd.cpp:424-432`). None affect a running game.
- World generation seeds `_random` from `generation_seed` — `genworld.cpp:107`.
- Script RNG (`GSBase.Rand*`) uses per-owner randomizers seeded from a copy of `_random` at world generation — `script_base.cpp:15-28`, `script_object.cpp:342-353`, `genworld.cpp:110`. Saved in savegames since `SLV_SCRIPT_RANDOMIZER`; only older saves re-seed — `afterload.cpp:3367-3369`.

### 4.2 Execution order
- Server frame: receive admin and clients → distribute queued commands (frame = max+1) → `_frame_counter++` → execute due commands → `StateGameLoop` → send — `network.cpp:1094-1281`. Commands are applied in a deterministic order at deterministic frames; this is the multiplayer lockstep contract.
- Scripts are budgeted by opcodes; the only wall-clock API is `GSDate.GetSystemTime()` (`time()`) — `script_date.cpp:63-68`. Grep found no other clock use in `src/script`, `src/game`, Squirrel.
- **Non-determinism sources to control:**
  1. *Admin message arrival tick.* Admin input is processed at the start of whichever frame the packet arrives. Send actions only while paused.
  2. *Link-graph lag pause.* If the threaded link-graph job is not done by its join tick, the server posts `CMD_PAUSE(LinkGraph)` until done — `linkgraphschedule.cpp:169-184`. Join timing, and therefore game state, stays deterministic in ticks. The number of paused frames is wall-clock dependent, which changes `GetTick()`, frame numbers and per-frame landscaping-limit refills (`openttd.cpp:1226`). Mitigate with `linkgraph.recalc_time` and all-manual distribution, or by keying replays on game ticks.
  3. `GSController.GetTick()` counts paused frames. Step by economy date or a counter of unpaused ticks instead.
  4. *Different binaries.* 15.3 fixes a desync where release and debug builds disagree because of unspecified evaluation order around `Random()` in train crashes (PR #15338, `changelog.md` 15.3). Pin one binary across all instances; prefer 15.3.

### 4.3 Save via RCON
- `save <name>` → `SaveOrLoad(name.sav, SLO_SAVE, …, SAVE_DIR)` — `console_cmds.cpp:420-436`. State is serialized to memory on the main thread (`SlSaveChunks`, `saveload.cpp:3056`); only compression and disk write may be threaded (`saveload.cpp:3060`). The snapshot is consistent at the RCON instant.
- With `gui.threaded_saves=true` (default) the RCON reply comes before the file is complete. **Set `gui.threaded_saves=false`** for synchronous saves — `saveload.cpp:3323`.

### 4.4 Load via RCON / CLI
- `load <file|index>` sets `SM_LOAD_GAME`, executed next game loop — `console_cmds.cpp:459-478`. On a server this triggers `NetworkReboot` → `NetworkServerStart`: frame counter reset to 0, local command queues freed (queued but unexecuted commands are lost), admins kept on dedicated — `openttd.cpp:1020-1046`, `network.cpp:919,976-993,623`.
- `-g file.sav` loads at startup; dedicated skips new-game generation — `openttd.cpp:578-600`, `dedicated_v.cpp:208-211`.
- `_pause_mode` and `TimerGameTick::counter` (u64) are saved — `misc_sl.cpp:86-87`. A save taken while GS-paused loads paused.

### 4.5 GS / AI state across save/load
- On save, the GS `Save()` result (a table) is stored — `script_instance.cpp:513-550`.
- On load, the GS config is restored and `GameInstance::Load(version)` stashes data — `game_sl.cpp:57-105`. After load, `StartScripts()` restarts every AI and the GS — `afterload.cpp:540-552`. `Game::StartNew` builds a **fresh VM** (constructor → `Load(version,data)` → `Start()`) — `game_core.cpp:72-100`, `script_instance.cpp:223-254`. In-flight suspended commands, locals and pending admin events are lost.
- **Fork fidelity rule:** to compare branches exactly, every branch, including the "continue" branch, must start by loading the same savegame. A parent that keeps running did not restart its scripts and can diverge from a loaded child.
- A second server can load the same file (`-D -g fork.sav` with its own `-c` dir and ports). This is exactly how multiplayer joins sync state, so the engine supports it by design.

### 4.6 Verdict
Given (binary, NewGRF/AI/GS set, settings, generation seed or start savegame, ordered commands stamped with execution tick), the simulation is deterministic. The GS is not needed for replay because its effects are ordinary commands.

---

## 5. Command logging and replay

- **`SERVER_CMD_LOGGING`** (type 8 @ Automatic): sent when a command is distributed for execution, before its outcome is known. Covers clients, server console, AI and GS commands — `network_command.cpp:350-352`. Payload: `uint32 client_id` (server/scripts = `CLIENT_ID_SERVER`=1), `uint8 company` (the GSCompanyMode company, or deity), `uint16 cmd_id`, `buffer args` (endian-serialized command tuple), `uint32 frame` = execution frame — `network_admin.cpp:606-619,1036-1045`. IDs and arg encoding are explicitly unstable across versions — `tcp_admin.h:487-492`. Fetch names with `ADMIN_POLL(CMD_NAMES=7)`.
  - Frames reset to 0 on each server start or load (`network.cpp:919`) and advance while paused. Frame numbers alone are not portable replay timestamps.
- **Desync log:** `-d desync=1` (or `debug_level desync=1`) writes `commands-out.log` in `save/autosave/` (`debug.cpp:113`). Lines include `cmd:`/`cmdf:` with economy date, date_fract, company, cmd id, err msg, hex args and name, written at execution time after the test run (`command.cpp:261-264`, `command_func.h:378-397`). Also `sync:` lines with `_random` state once per day (`network.cpp:1101-1109`), `save:`/`load:` lines, and a `dmp_cmds_<seed>_<date>.sav` right after map generation (`genworld.cpp:216-221`). Level ≥3 adds saves every 32 days (`openttd.cpp:1247-1251`).
- **Native replay:** only with `#define DEBUG_DUMP_COMMANDS` (commented out at `network_func.h:17`). Reads `commands.log` from the save dir, injects each command at its (date, date_fract), verifies `sync:` states, runs at max speed, and disables AI/GS loops — `network.cpp:1111-1232`, `video_driver.hpp:300-304`, `openttd.cpp:1227,1271`.
- **Recommended Railhead trajectory format:** start savegame (or seed + config + content hashes) + commands-out.log-style records keyed by economy date and date_fract or tick counter + per-step GS-emitted observation/reward summaries. `CMD_LOGGING` frames suit live auditing; date or tick keys suit replay.

---

## 6. Headless new-game configuration

### 6.1 Command line (`openttd.cpp:477-648`)
| Flag | Effect |
|---|---|
| `-D [host][:port]` | Dedicated server; sets null music/sound/blitter, video `dedicated`, `net=4` debug; optional bind address/port |
| `-g [file]` | Load savegame/scenario/heightmap; with no file → new random game |
| `-G <seed>` | Generation seed (only way besides `newgame <seed>`) |
| `-t <year>` | Starting year |
| `-c <file>` | Config file; its folder becomes the working search path, and `private.cfg`/`secrets.cfg` live beside it — `fileio.cpp:814-832,906-908,943-946` |
| `-x` | Don't save config on exit |
| `-X` | Use only local paths (no personal dir) |
| `-q <file>` | Print savegame info and exit |
| `-Q` | Skip NewGRF scanning |
| `-d <lvl>` | Debug levels, e.g. `-d desync=1,script=4` |
| `-f` | Fork to background (UNIX) |

The dedicated server also reads console commands from stdin each loop — `dedicated_v.cpp:173-192`. Spawn with `stdin=PIPE` rather than `/dev/null`.

### 6.2 Config files
Section = text before the first `.` of the setting name — `settings.cpp:632-634`. If `openttd.cfg` has no `[version] ini_version`, private/secret settings are also read from it — `settings.cpp:1391-1392`.

```ini
; openttd.cfg
[game_creation]
map_x = 8              ; log2, 6..12  (map_type.h:37-38)
map_y = 8
landscape = temperate  ; temperate|arctic|tropic|toyland (old_gameopt_settings.ini:18)
starting_year = 1950
land_generator = 1
; generation_seed is NotInConfig -> use -G (world_settings.ini:200-208)
[difficulty]
max_no_competitors = 0
number_towns = 2
industry_density = 4
max_loan = 300000
initial_interest = 2
vehicle_costs = 0
construction_cost = 0
terrain_type = 1
quantity_sea_lakes = 0
economy = false
vehicle_breakdowns = 0
subsidy_multiplier = 2
disasters = false
[construction]
command_pause_level = 3          ; allow all commands while paused
[script]
script_max_opcode_till_suspend = 250000
script_max_memory_megabytes = 1024
[ai]
ai_in_multiplayer = true
[network]
server_admin_port = 3977
allow_insecure_admin_login = true    ; localhost only
frame_freq = 0
commands_per_frame_server = 1024
[gui]
threaded_saves = false
autosave_interval = 0
[game_scripts]
RailheadBridge = key1=1,key2=0       ; first item only (settings.cpp:980-1002); value format name=int,... (script_config.cpp:142-162)
[ai_players]
none =                               ; one line per AI slot, in company order; "none" keeps the slot without a named script (settings.cpp:951-978). Which AI then starts in that slot is UNVERIFIED (likely random).

; secrets.cfg (same dir as openttd.cfg when -c is used)
[network]
admin_password = <random>

; private.cfg
[network]
server_name = railhead-0
```
Defaults and ranges were extracted from `difficulty_settings.ini`, `world_settings.ini`, `script_settings.ini`, `network_settings.ini` (§2.3 and `linkgraph.*` in §4.2). GS/AI files go in `<cfgdir>/game/` and `<cfgdir>/ai/` (`fileio.cpp:49-52`). `save/` must exist in the working dir or writes fall through to the next search path (`fileio.cpp` `FioFOpenFile`).

---

## 7. Player command coverage via GameScript (GSCompanyMode, 15.2)

### 7.1 Method and definitions
- Source of truth: the `Commands` enum, 145 entries — `src/command_type.h:196-379`. Flags and command types come from each `DEF_CMD_TRAIT` line (`src/*_cmd.h`).
- **Player-facing** = a human client can send it for its own company. The server rejects `Server`-flagged commands from clients and requires `cp.company == client_playas` — `network_server.cpp:1080-1099`. `Offline` commands are single-player only — `command_type.h:409`. `Deity` means "deity *may also* issue it" — `command_type.h:415`. Cmd procs decide the rest, e.g. goals reject non-deity.
- **Script mapping:** every `Command<CMD_X>` call in `src/script/api/*.cpp` was attributed to its enclosing function, and that function's `Enforce*` macro gives the mode. 110 of 145 commands are reachable from some script function; 35 are reachable from none.
- **Legend:**
  - YES = issued by a GS function under `GSCompanyMode`.
  - EQUIV = no 1:1 call, but the same end state is reachable through other calls (more commands, more ticks).
  - NO = not reachable from a GameScript at all.
  - Mode C = `EnforceCompanyModeValid`; D|C = deity or company.

**Summary for the 100 player-facing commands:** 68 YES, 11 EQUIV, 21 NO. The 45 non-player commands are server, deity, offline-cheat or internal (§7.3).

### 7.2 Player-facing commands

**Rail, signals, waypoints**
| Command | Status | GS function (evidence) | Mode | Notes |
|---|---|---|---|---|
| BUILD_RAILROAD_TRACK | YES | `GSRail.BuildRail` (script_rail.cpp:366), `BuildRailTrack` (:254) | C | |
| REMOVE_RAILROAD_TRACK | YES | `RemoveRail` (:383), `RemoveRailTrack` (:265) | C | |
| BUILD_SINGLE_RAIL | EQUIV | `BuildRailTrack` | C | Uses RAILROAD_TRACK for one tile |
| REMOVE_SINGLE_RAIL | EQUIV | `RemoveRailTrack` | C | |
| BUILD_SINGLE_SIGNAL | YES | `GSRail.BuildSignal` (:469) | C | Always electric, never semaphore; cannot convert or cycle an existing signal |
| REMOVE_SINGLE_SIGNAL | YES | `RemoveSignal` (:488) | C | |
| BUILD_SIGNAL_TRACK | EQUIV | loop `BuildSignal` | C | No drag or autofill density |
| REMOVE_SIGNAL_TRACK | EQUIV | loop `RemoveSignal` | C | |
| BUILD_TRAIN_DEPOT | YES | `BuildRailDepot` (:148) | C | |
| BUILD_RAIL_STATION | YES | `BuildRailStation` (:162); `BuildNewGRFRailStation` (:199/203) | C | Default station class only (`STAT_CLASS_DFLT`, :161). The NewGRF variant picks a spec via callback; scripts cannot freely choose class or type |
| REMOVE_FROM_RAIL_STATION | YES | `RemoveRailStationTileRectangle` (:232) | C | |
| BUILD_RAIL_WAYPOINT | YES | `BuildRailWaypoint` (:214) | C | 1×1, default waypoint class |
| REMOVE_FROM_RAIL_WAYPOINT | YES | `RemoveRailWaypointTileRectangle` (:223) | C | |
| CONVERT_RAIL | YES | `ConvertRailType` (:120) | C | |
| RENAME_WAYPOINT | YES | `GSBaseStation.SetName` (script_basestation.cpp:58) | C | |
| MOVE_WAYPOINT_NAME | NO | — | | Cosmetic |

**Road and tram** (trams: `GSRoad.SetCurrentRoadType(<tram type>)`, then the same calls)
| Command | Status | GS function (evidence) | Mode | Notes |
|---|---|---|---|---|
| BUILD_LONG_ROAD | YES | `BuildRoad`/`BuildRoadFull`/`BuildOneWayRoad(Full)` → `_BuildRoadInternal` (script_road.cpp:531) | D\|C (one-way: C) | |
| REMOVE_LONG_ROAD | YES | `RemoveRoad` (:606), `RemoveRoadFull` (:618) | C | |
| BUILD_ROAD | EQUIV | only used for bridge/tunnel road ends (script_bridge.cpp:109,123; script_tunnel.cpp:111,125) | | Normal road placement goes through LONG_ROAD |
| BUILD_ROAD_DEPOT | YES | `BuildRoadDepot` (:567) | C | |
| BUILD_ROAD_STOP | YES | `BuildRoadStation`, `BuildDriveThroughRoadStation` → (:584) | C | 1×1, default stop class (`ROADSTOP_CLASS_DFLT`); no drag rectangles or NewGRF stop types |
| REMOVE_ROAD_STOP | YES | `RemoveRoadStation` (:638) | C | |
| BUILD_ROAD_WAYPOINT | **NO** | — | | |
| REMOVE_FROM_ROAD_WAYPOINT | **NO** | — | | |
| CONVERT_ROAD | YES | `ConvertRoadType` (:135) | C | |

**Water, air, bridges, tunnels**
| Command | Status | GS function (evidence) | Mode | Notes |
|---|---|---|---|---|
| BUILD_DOCK | YES | `GSMarine.BuildDock` (script_marine.cpp:95) | C | |
| BUILD_SHIP_DEPOT | YES | `BuildWaterDepot` (:86) | C | |
| BUILD_BUOY | YES | `BuildBuoy` (:103) | C | |
| BUILD_CANAL | YES | `BuildCanal` (:119) | C | |
| BUILD_LOCK | YES | `BuildLock` (:111) | C | |
| BUILD_AIRPORT | YES | `GSAirport.BuildAirport` (script_airport.cpp:80) | C | Layout argument fixed to 0, so no rotated layouts |
| OPEN_CLOSE_AIRPORT | YES | `GSStation.OpenCloseAirport` (script_station.cpp:244) | C | |
| BUILD_BRIDGE | YES | `GSBridge.BuildBridge` (script_bridge.cpp:89-93) | D\|C | Bridge type selectable |
| BUILD_TUNNEL | YES | `GSTunnel.BuildTunnel` (script_tunnel.cpp:93,96) | D\|C (rail: C) | |

**Land, clearing, trees, objects**
| Command | Status | GS function (evidence) | Mode | Notes |
|---|---|---|---|---|
| TERRAFORM_LAND | YES | `GSTile.RaiseTile`/`LowerTile` (script_tile.cpp:265,273) | C | |
| LEVEL_LAND | YES | `GSTile.LevelTiles` (:282) | C | Level mode only (`LM_LEVEL`); area raise/lower modes need RaiseTile/LowerTile loops |
| LANDSCAPE_CLEAR | YES | `GSTile.DemolishTile` (:290) and every `Remove*` for airports, bridges, tunnels, docks, depots, buoys, canals, locks | D\|C / C | |
| CLEAR_AREA | EQUIV | loop `DemolishTile` | | |
| PLANT_TREE | YES | `PlantTree`, `PlantTreeRectangle` (:298,310) | C | Tree type random (`TREE_INVALID`); GUI lets you choose |
| BUILD_OBJECT | YES | `GSObjectType.BuildObject` (script_objecttype.cpp:48), `GSCompany.BuildCompanyHQ` (script_company.cpp:304) | D\|C / C | |
| BUILD_OBJECT_AREA | EQUIV | loop `BuildObject` | | |

**Vehicles**
| Command | Status | GS function (evidence) | Mode | Notes |
|---|---|---|---|---|
| BUILD_VEHICLE | YES | `GSVehicle.BuildVehicle`, `BuildVehicleWithRefit` (script_vehicle.cpp:85) | C | Blocked if `ai.ai_disable_veh_<type>` |
| SELL_VEHICLE | YES | `SellVehicle` (:175), `SellWagon`/`SellWagonChain` (:187) | C | |
| CLONE_VEHICLE | YES | `CloneVehicle` (:116) | C | |
| REFIT_VEHICLE | YES | `RefitVehicle` (:165) | C | Cargo only. Subtype is fixed at 0 and the whole vehicle is refitted, so there are no NewGRF refit subtypes or partial-consist refits |
| START_STOP_VEHICLE | YES | `StartStopVehicle` (:233) | C | |
| SEND_VEHICLE_TO_DEPOT | YES | `SendVehicleToDepot` (:205), `...ForServicing` (:213) | C | No mass-send |
| MOVE_RAIL_VEHICLE | YES | `MoveWagon`, `MoveWagonChain` (:138) | C | Consist editing |
| REVERSE_TRAIN_DIRECTION | YES | `ReverseVehicle` (:244) | C | |
| TURN_ROADVEH | YES | `ReverseVehicle` (:243) | C | |
| RENAME_VEHICLE | YES | `GSVehicle.SetName` (:260) | C | |
| MASS_START_STOP | EQUIV | loop `StartStopVehicle` | | |
| DEPOT_SELL_ALL_VEHICLES | EQUIV | loop `SellVehicle` | | |
| DEPOT_MASS_AUTOREPLACE | **NO** | — | | |
| CHANGE_SERVICE_INT | **NO** | — | | No getter or setter for service intervals anywhere in the script API |
| FORCE_TRAIN_PROCEED | **NO** | — | | |
| SET_VEHICLE_VISIBILITY | NO | — | | Cosmetic: hides an engine in purchase lists |
| WANT_ENGINE_PREVIEW | **NO** | only `GSEventEnginePreview.AcceptPreview` (script_event_types.cpp:110), class is `@api ai` (script_event_types.hpp:241) | | GS never sees preview offers |

**Orders and timetables**
| Command | Status | GS function (evidence) | Mode | Notes |
|---|---|---|---|---|
| INSERT_ORDER | YES | `GSOrder.AppendOrder`/`InsertOrder` (script_order.cpp:530), `Append/InsertConditionalOrder` (:548) | C | Flags: non-stop, unload/transfer/no-unload, full-load/any, no-load, service-if-needed, stop-in-depot, nearest depot (script_order.hpp:46-81) |
| DELETE_ORDER | YES | `RemoveOrder` (:559) | C | |
| MODIFY_ORDER | YES | `SetOrderFlags` (:615-633), `SetOrderCondition`/`CompareFunction`/`CompareValue`/`JumpTo` (:394-428), `SetStopLocation` (:442) | C | |
| SKIP_TO_ORDER | YES | `SkipToOrder` (:570) | C | |
| MOVE_ORDER | YES | `MoveOrder` (:668) | C | |
| CLONE_ORDER | YES | `CopyOrders`/`ShareOrders`/`UnshareOrders` (:677,686,694) | C | |
| ORDER_REFIT | YES | `SetOrderRefit` (:452) | C | |
| CHANGE_TIMETABLE | **NO** | — | | No timetable API at all, read or write |
| BULK_CHANGE_TIMETABLE | **NO** | — | | |
| SET_VEHICLE_ON_TIME | **NO** | — | | |
| AUTOFILL_TIMETABLE | **NO** | — | | |
| SET_TIMETABLE_START | **NO** | — | | |

**Groups, autoreplace, autorenew**
| Command | Status | GS function (evidence) | Mode | Notes |
|---|---|---|---|---|
| CREATE_GROUP | YES | `GSGroup.CreateGroup` (script_group.cpp:37) | C | |
| DELETE_GROUP | YES | `DeleteGroup` (:48) | C | |
| ALTER_GROUP | YES | `SetName` (:69), `SetParent` (:85) | C | |
| ADD_VEHICLE_GROUP | YES | `MoveVehicle` (:138) | C | |
| ADD_SHARED_VEHICLE_GROUP | EQUIV | loop `MoveVehicle` | | |
| REMOVE_ALL_VEHICLES_GROUP | EQUIV | loop `MoveVehicle` | | |
| SET_GROUP_FLAG | YES | `EnableAutoReplaceProtection` (:101) | C | Replace-protection flag only |
| SET_GROUP_LIVERY | YES | `SetPrimaryColour`/`SetSecondaryColour` (:228,236) | C | |
| SET_AUTOREPLACE | YES | `SetAutoReplace`/`StopAutoReplace` (:161,177) | C | |
| CHANGE_COMPANY_SETTING | YES (partial) | `GSCompany.SetAutoRenewStatus/Months/Money` (script_company.cpp:319,335,351), `GSGroup.EnableWagonRemoval` (script_group.cpp:146) | C | Only those four company settings; others, e.g. default service intervals, are not settable |

**Company and finance**
| Command | Status | GS function (evidence) | Mode | Notes |
|---|---|---|---|---|
| INCREASE_LOAN / DECREASE_LOAN | YES | `GSCompany.SetLoanAmount` (script_company.cpp:265,267) | C | |
| RENAME_COMPANY | YES | `SetName` (:73) | C | |
| RENAME_PRESIDENT | YES | `SetPresidentName` (:94) | C | |
| SET_COMPANY_COLOUR | YES | `SetPrimaryLiveryColour`/`SetSecondaryLiveryColour` (:365,371) | C | |
| SET_COMPANY_MANAGER_FACE | YES (partial) | `SetPresidentGender` (:122) | C | Gender only |
| GIVE_MONEY | **NO** | — | | |
| BUY_COMPANY | **NO** | only `GSEventCompanyAskMerger.AcceptMerger` (script_event_types.cpp:116), `@api ai` (script_event_types.hpp:442) | | Cannot buy bankrupt rivals |
| COMPANY_CTRL | NO | — | | New company, join or delete. Workaround: RCON `start_ai` (§2.1) |
| COMPANY_ALLOW_LIST_CTRL | NO | — | | Multiplayer access control, not gameplay |

**Towns, industries, names, signs**
| Command | Status | GS function (evidence) | Mode | Notes |
|---|---|---|---|---|
| DO_TOWN_ACTION | YES | `GSTown.PerformTownAction` (script_town.cpp:276) | C | Advertise, road works, statue, fund buildings, exclusive rights, bribe |
| BUILD_INDUSTRY | YES | `GSIndustryType.BuildIndustry`/`ProspectIndustry` (script_industrytype.cpp:127,136) | D\|C | Funding and prospecting |
| FOUND_TOWN | YES | `GSTown.FoundTown` (script_town.cpp:317) | D\|C | Players only if `economy.found_town` ≠ forbidden (default forbidden; town_cmd.cpp CmdFoundTown, economy_settings.ini:72-76) |
| PLACE_HOUSE | NO | — | | Players only if `economy.place_houses` allows it (default forbidden; town_cmd.cpp:2949, economy_settings.ini:87-90) |
| RENAME_STATION | YES | `GSBaseStation.SetName` (script_basestation.cpp:56) | C | |
| PLACE_SIGN | YES | `GSSign.BuildSign` (script_sign.cpp:84) | D\|C | |
| RENAME_SIGN | YES | `GSSign.SetName` (:48); `RemoveSign` (:70) renames to empty | D\|C | |
| MOVE_STATION_NAME / RENAME_DEPOT / MOVE_SIGN | NO | — | | Cosmetic |

### 7.3 Non-player commands (45)
- **Server-only:** PAUSE, CHANGE_SETTING, RENAME_ENGINE, RENAME_TOWN (also deity). PAUSE is exposed as `GSGame.Pause`; settings as `GSGameSettings.SetValue` (game-only).
- **Offline cheats or editor:** MONEY_CHEAT, DELETE_TOWN.
- **Deity tools (GS as game master):** CHANGE_BANK_BALANCE, SET_COMPANY_MAX_LOAN, ENGINE_CTRL, INDUSTRY_SET_{FLAGS, EXCLUSIVITY, TEXT, PRODUCTION}, TOWN_{CARGO_GOAL, GROWTH_RATE, RATING, SET_TEXT}, EXPAND_TOWN, CREATE_SUBSIDY, CUSTOM_NEWS_ITEM, 8 goal commands, 8 story-page commands, 5 league-table commands, SCROLL_VIEWPORT. All exposed to the GS in deity mode via `GSGoal`, `GSStoryPage`, `GSLeagueTable`, `GSTown`, `GSIndustry`, `GSSubsidy`, `GSNews`, `GSViewport`, `GSEngine` and `GSCompany` (all `EnforceDeityMode`). Useful for scenario design and reward signalling, not as agent actions.
- **Player clicks on GS-made UI:** GOAL_QUESTION_ANSWER, STORY_PAGE_BUTTON.
- **Internal:** AUTOREPLACE_VEHICLE, issued by the engine when a vehicle enters a depot (vehicle.cpp:1060), and CLEAR_ORDER_BACKUP (order_backup.cpp:154,183).

### 7.4 Gameplay-relevant gaps (GS route)
1. **Timetables:** none of the 5 commands, and no timetable getters.
2. **Service intervals:** no getter or setter (CHANGE_SERVICE_INT, and company servint settings via CHANGE_COMPANY_SETTING).
3. **Road waypoints:** build and remove.
4. **Signals:** no semaphores, no converting or cycling existing signals, no drag or autofill.
5. **Station and stop variety:** default classes only; no free choice of NewGRF station or road-stop type; no airport layout rotation; 1×1 road stops.
6. **Refit:** no subtypes, no partial consist.
7. **Missed offers:** engine previews and merger offers. Their events are AI-only, so the agent company never accepts them.
8. **Other:** FORCE_TRAIN_PROCEED, GIVE_MONEY, DEPOT_MASS_AUTOREPLACE, tree type choice, level-land raise/lower modes.

### 7.5 Options for exact human parity
| Option | What it gives | Cost and risk | Evidence |
|---|---|---|---|
| A. GS API only | 79/100 player commands (YES + EQUIV), typed and validated | Gaps in §7.4; returned IDs only in sync mode | §7.2 |
| B. Engine patch: new admin packet `ADMIN_COMMAND{uint8 company, uint16 cmd, buffer args}` that applies the same checks as `Receive_CLIENT_COMMAND` (valid, not `Server`/`Offline`, company exists, `_cmd_dispatch[cmd].Sanitize`) and calls `NetworkSendCommand(cmd, STR_NULL, nullptr, company, args)` | All 100 player commands, through the same validation and execution path as a human client; logged by CMD_LOGGING and the desync log | ~40 lines C++. Python needs a per-command argument encoder: the `EndianBufferWriter` serialization of each `CommandTraits<CMD>::Args` tuple, which is version-specific (pin the binary). Results need a reply path (desync log `cmd`/`cmdf`, or a patched result packet) | `network_server.cpp:1054-1100`, `network_command.cpp:206-233,374-388`, `tcp_admin.h:487-492` |
| C. Headless Python game client (no engine patch): join as a real client, own a company, send `PACKET_CLIENT_COMMAND{uint8 company, uint16 cmd, uint16 err_msg, buffer args, uint8 callback}` | Literally the human action set | Must implement game-protocol auth/encryption (server always creates an X25519 auth handler, `network_server.cpp:447`), map download, and frame ACKs within lag limits. Whether a non-simulating client stays connected is UNVERIFIED. Same per-command encoders as B | `network_command.cpp:374-388`, `network_server.cpp:1054-1100` |

**Recommendation:** B for actions plus GS for observations, rewards and stepping. B keeps one validation path identical to human play. The GS typed API remains a convenient fallback and a reference encoder: compare against CMD_LOGGING output for the same action.

### 7.6 What a human sees that a script cannot query (15.2)
Verified by listing every `static` function in the relevant `script_*.hpp` and grepping for timetable, service, lost and signal-state names (no hits).
| Human-visible information | Script access |
|---|---|
| Timetable (travel and wait times, lateness, start date) | None |
| Vehicle service interval, "lost" flag/news | None. `GSEventVehicleLost` is `@api ai` |
| Signal aspect (red/green), PBS path reservations | None (`GSRail.GetSignalType` only) |
| Vehicle sub-tile position, heading, exact sprite location | Tile only (`GSVehicle.GetLocation` returns TileIndex, script_vehicle.hpp:161), plus speed and state |
| Cargo in vehicle by source and transit age, feeder share | Load per cargo only (`GetCargoLoad`) |
| Finance window categories (construction, new vehicles, running costs, property maintenance, loan interest, income per transport type) | Quarterly totals only (`GetQuarterlyIncome/Expenses`), monthly infrastructure costs, `GSAccounting` for the script's own commands |
| Performance rating breakdown (detailed rating window) | Total score only (`GetQuarterlyPerformanceRating`) |
| Station rating components | Final rating only (`GetCargoRating`) |
| News feed: engine available or preview, vehicle unprofitable, waiting in depot, aircraft destination too far, autoreplaced, zeppelin crash, merger offers | Not delivered to GS (event classes are `@api ai`: script_event_types.hpp:241,442 and others). Partially pollable: engine lists, profits, `IsStoppedInDepot`, `GetMaximumOrderDistance` |
| Engine-preview and merger offer dialogs | Not delivered, not answerable |
| Rendered map and smallmap overlays (contours, vehicles, industries, link graph, routes, owners) | Per-tile queries (§2.5) and `GSStation.GetCargoPlanned*` for flows; no imagery |
| Exact tile decoration (house type and name, tree count/type, NewGRF object or station graphics) | `IsHouseTile`, `HasTreeOnTile`, terrain/slope/owner/transport type |

---

## 8. Map images from a headless dedicated server

| Type (`screenshot <type> [<name>]`) | Content | Works with stock `-D`? | Evidence |
|---|---|---|---|
| `minimap` | 1 px per tile, 32 bpp. Owner view only: company colours, industries dark grey, houses/town roads dark red, water, else land; no vehicles; rail and road not distinguished | **Yes, from code.** No blitter needed; company colours come from recolour sprites loaded after generation and load (`genworld.cpp:341`, `afterload.cpp:249`, `main_gui.cpp:553-561`); legend rebuilt on company creation and load (`company_cmd.cpp:646`, `afterload.cpp:310`). **Runtime-verified on 15.3 (§13)** | `screenshot.cpp:473-503`, `smallmap_gui.cpp:580-607` |
| `heightmap` | 8 bpp greyscale, 1 px per tile, normalized to highest peak | **Yes; runtime-verified on 15.3 (§13)** | `screenshot.cpp:314-333` |
| `viewport`, `normal`, `big`, `giant` | Sprite-rendered isometric images; `giant` covers the whole map at normal zoom | **No** with stock `-D`. `-D` forces the `null` blitter (`openttd.cpp:540`), which reports depth 0 (`blitter/null.hpp:18`), and image writers accept only 8/32 bpp (`screenshot_png.cpp:44`; BMP switch at `screenshot_bmp.cpp:49`), so the result is "Screenshot failed!" | `screenshot.cpp:274-285` |
| same, with `-D -b 32bpp-simple` (blitter flag **after** `-D`) | Full rendered image | **Not with a dedicated-only build** (`-DOPTION_DEDICATED=ON` compiles no 8/32 bpp blitters, `src/blitter/CMakeLists.txt:1-16`; observed hang on a macOS error dialog, §13). With a full build it is plausible but UNVERIFIED: option parsing lets a later `-b` override `-D`'s blitter (`openttd.cpp:535-540`), and the dedicated driver allocates a video buffer when depth ≠ 0 (`dedicated_v.cpp:109-110`). Needs the base graphics set. `giant` is very large: whole map at `ZoomLevel::WorldScreenshot = Normal` (`zoom_type.h:42`), roughly 32·(X+Y) × 16·(X+Y) px, ≈16k×8k for 256×256 (estimate) | `screenshot.cpp:274-285`, `SetupScreenshotViewport` SC_WORLD branch |

Operational notes:
- **Availability:** `screenshot` has no console hook, so it works on a dedicated server and over RCON — `console_cmds.cpp:2895`.
- **Asynchronous:** `MakeScreenshot` queues the work on the main thread (`screenshot.cpp:456-470`); the dedicated loop drains it next iteration (`dedicated_v.cpp:219`). RCON_END arrives **before** the file exists.
- **Completion signal:** the result is printed to the console through `ShowErrorMessage` (`error_gui.cpp:314-330`) as "Screenshot successfully saved as '<file>'", "Heightmap successfully saved as …", or "Screenshot failed!" (`english.txt:5024-5026`). Subscribe to CONSOLE (type 6 @ 0x40) to detect it.
- **Output location and format:** `<working dir>/screenshot/` (`fileio.cpp:53`). Format via `[misc] screenshot_format = png|bmp|pcx` (`misc_settings.ini:168-171`; the first provider is used if unset, `screenshot.cpp:49-58`). A filename ending in `#` auto-numbers.
- **For model observations:** the minimap is cheap pixel context. Structured observations should still come from GS tile scans (§2.5), since the minimap has no rail/road/vehicle detail.

---

## 9. Recommended bridge architecture

```
Python env (Gymnasium) ──TCP 127.0.0.1:admin_port──► openttd -D (patched: gamespeed)
  AdminClient                                          │
   ├ JOIN (insecure, localhost)                        ├ GS "RailheadBridge" (deity loop)
   ├ UPDATE_FREQUENCY GAMESCRIPT=9@0x40, CMD_LOGGING=8@0x40, CONSOLE as needed
   ├ RCON: start_ai RailheadIdle, gamespeed 0, save/load, quit
   └ ADMIN_GAMESCRIPT  ◄──► GSAdmin.Send (≤1400 B chunks) / GSEventAdminPort
```
1. **One process per env instance:** `openttd -D 127.0.0.1:<p> -c <inst>/openttd.cfg -G <seed> -x -X [-g start.sav]`, plus distinct `server_port` and `server_admin_port`. Identical pinned binary everywhere (prefer 15.3 + patch).
2. **Bootstrap:** wait for `WELCOME`; RCON `start_ai RailheadIdle` (an AI whose `Start()` loops `Sleep(1000)`). That yields agent company `cid`. RCON `gamespeed 0`. Wait for a GS `{"t":"ready"}` handshake, since events sent before the GS starts are dropped.
3. **Lockstep step protocol (GS-driven):**
   - The GS owns pausing: it calls `GSGame.Pause()` at step boundaries.
   - While paused, Python sends one compact action JSON (≤8999 B, ASCII, no floats, no newlines).
   - Under `GSCompanyMode(cid)` the GS runs sync commands where IDs are needed (`BuildVehicle`) and `GSAsyncMode` for bulk construction. It replies `{"t":"ack","id":…,"results":[…]}`, then `Unpause()`.
   - The GS counts K unpaused ticks or waits for the target economy date, then pauses. It streams the observation (cargo-monitor deltas, balance, quarterly stats, per-tile diffs) as `{"t":"obs","seq":i,"n":N,…}` chunks of ≤1400 B.
   - Requires `command_pause_level=3`. Determinism holds because all inputs land on paused frames.
   - For full human parity, route agent actions through the raw-command admin packet (§7.5 option B) instead of GS calls. The GS keeps owning pause/step, observations and rewards.
4. **Rewards:** `GSCargoMonitor` per (company, cargo, industry or town) plus `GSCompany` balance and quarterly values, cross-checked against admin `COMPANY_ECONOMY` polls (independent of GS code).
5. **Logging:** keep `CMD_LOGGING` live as the action audit, run `-d desync=1` for a date-keyed replay log, and store a savegame at each episode start.
6. **Forks:** at a paused boundary, RCON `save fork_<k>` (synchronous with `threaded_saves=false`), then start M servers with `-g fork_<k>.sav`. The parent also `load`s the same file. The GS `Save()`/`Load()` must persist all step-protocol state.
7. **Bulk observations:** prefer GS-side diffs. Full-map scans are expensive; at 250k ops/tick a 256×256 scan spans multiple ticks while paused (frames are free while paused, but cost 27 ms each unless patch 2 is applied).

---

## 10. Risks

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | GS→admin JSON has **no size guard**; >1456 B (1440 encrypted) crashes assert-enabled builds or emits an oversized packet | High | Hard 1400 B chunking in the GS library; never ship an assert build without the guard; or patch `ScriptAdmin::Send` to reject |
| 2 | Inbound JSON: `\n\t\r` become `?`, floats rejected, root must be an object, >8999 B truncated | Med | Compact `json.dumps(separators=(',',':'), ensure_ascii=True)`; ints only (fixed-point) |
| 3 | No fast-forward on dedicated; null driver cannot serve | High | Maintain the `gamespeed` patch (and paused-interval patch); re-apply after every load or new game |
| 4 | Paused frames cost 27 ms each; ≤1 sync GS command per frame | Med | Async mode for bulk; paused-interval patch; batch actions |
| 5 | `command_pause_level<3` makes GS sync commands block during pause (silent deadlock) | High | Set `construction.command_pause_level=3`; GS watchdog |
| 6 | Link-graph lag pauses are wall-clock dependent and shift frames and `GetTick` | Med | Key replays by date/ticks; tune `linkgraph.recalc_time`; avoid `GetTick`/`GetSystemTime` in GS logic |
| 7 | Load restarts GS and AI VMs, drops queued commands and pending events; frame counter resets | Med | Fork rule "all branches load"; GS fully serializes state; only fork while paused with an empty queue |
| 8 | Cross-binary desync (fixed in 15.3, #15338) | Med | Pin a single binary/platform; use 15.3 |
| 9 | Agent company is an AI company (`is_ai`): its AI script is restarted on load, and `ai_disable_veh_*` also restricts the GS. Hostile takeover of AI companies is blocked in network games (`economy.cpp:2034-2035`) | Low | Idle AI script; never disable vehicle types for competitors |
| 10 | No GS API to create companies | Low | `start_ai` via RCON with `ai_in_multiplayer=true` |
| 11 | `CMD_LOGGING` cmd IDs and arg encoding are version-unstable | Low | Pin version; resolve via `CMD_NAMES`; prefer GS-level semantic action logs |
| 12 | Plain admin login disabled by default; secure login needs Monocypher-compatible crypto (UNVERIFIED Python support) | Low | Insecure JOIN bound to 127.0.0.1, random password per instance |
| 13 | Threaded save races with copying the file | Med | `gui.threaded_saves=false` |
| 14 | Per-company landscaping limits throttle terraform-heavy policies | Low | Raise `construction.*_per_64k_frames` / `*_frame_burst` |
| 15 | GS opcode/memory limits are NewgameOnly | Low | Set in cfg before generation; they are saved with the game |
| 16 | `-c` puts `private.cfg`, `secrets.cfg`, `save/`, `game/`, `ai/` in the cfg folder; a missing `save/` falls through to the personal dir | Low | Per-instance dir with pre-created subdirs; `-X` to avoid the personal dir |
| 17 | Admin auth timeout 10 s; RCON content commands are async | Low | Connect promptly; avoid `content` over RCON |
| 18 | **GS route lacks 21 of 100 player commands** (timetables, service intervals, road waypoints, semaphores/signal conversion, engine previews, mergers, force-proceed, give money, …) — violates "same action set as a human" | High | §7.5 option B (raw-command admin packet with client-equivalent validation) or option C (headless game client) |
| 19 | Raw-command path (B/C) needs per-command argument encoders tied to the exact binary | Med | Generate encoders from `CommandTraits<>::Args` in the pinned source; cross-check against CMD_LOGGING bytes from GS-issued equivalents |
| 20 | Rendered screenshots fail under stock `-D` (null blitter) and are impossible in a dedicated-only build (no 8/32 bpp blitters compiled); screenshots complete asynchronously after RCON_END | Low | Use `minimap`/`heightmap` (runtime-verified, §13); wait for the console "successfully saved" line |
| 21 | Dedicated-server stdin console reads lines through `std::cin` after a raw `select()`, so lines arriving together get stuck in the C++ buffer until more input arrives (observed at runtime) | Low | Drive the console only via admin RCON, never via stdin |
| 22 | Project `wire.py` encoder allows outbound frames up to 65535 bytes, but the server drops any connection whose packet exceeds 32767 (`packet.cpp:267`, `tcp.cpp:126`) | Low | Cap outbound frames at 32767 in `wire.py` (admin→GS JSON is capped at 8999 anyway) |

---

## 11. (A) Player command coverage — re-verified on 15.3

The full per-command table (all 100 player-facing commands, each with its GS function under `GSCompanyMode` or NOT EXPOSED) is **§7.2**. The non-player commands are in §7.3, the gaps in §7.4, and what a human sees that scripts cannot query in §7.6. This section re-checks it against **15.3** and adds the detail requested for signals, order flags, timetables, groups, autoreplace and autorenew, refit, town actions and company settings.

**15.3 delta.** I diffed `src/` of 15.2 (c91185eb) against 15.3 (14ec60f2). The files that matter here are unchanged: `src/command_type.h`, every `*_cmd.h`, and every `src/script/api/*` file except one. That exception is `script_tile.cpp:51`, where `IsBuildableRectangle` now rejects 0×0 rectangles. Other 15.3 changes (aircraft, order condition clamping, the train-crash evaluation order, GUI) do not change the API. **The §7.2 counts hold for 15.3: 68 YES, 11 EQUIV, 21 NOT EXPOSED.** Line numbers in §7 are identical in 15.3 for all script API files except `script_tile.cpp`.

### 11.1 Signals (`GSRail`)
| Human option | Script | Evidence |
|---|---|---|
| Block, entry, exit, combo, path (PBS), one-way path signals | `BuildSignal(tile, front, SIGNALTYPE_NORMAL/ENTRY/EXIT/COMBO/PBS/PBS_ONEWAY)` | script_rail.hpp:70-75 |
| Two-way variants | `SIGNALTYPE_*_TWOWAY` (bit 8) | script_rail.hpp:77-81 |
| Semaphore vs electric | **NOT EXPOSED**: always `SIG_ELECTRIC` | script_rail.cpp:469 |
| Convert or cycle an existing signal, drag with density, autofill | **NOT EXPOSED** (remove and rebuild, or loop per tile) | CMD_BUILD_SIGNAL_TRACK is not referenced in script api |
| Remove signal | `RemoveSignal` | script_rail.cpp:488 |
| Read signal type | `GetSignalType` | script_rail.hpp |
| Read signal aspect or reservation | **NOT EXPOSED** | no getter |

### 11.2 Order flags (`GSOrder`)
Mapping from script flags to engine fields — script_order.hpp:46-81, script_order.cpp:484-530; engine enums at order_type.h:67-137.
| Human GUI option | Script flag or call | Engine field |
|---|---|---|
| Non-stop | `OF_NON_STOP_INTERMEDIATE` | `OrderNonStopFlag::NoIntermediate` |
| **Go via** ("Go via" / "Go non-stop via", english.txt:4638-4639) | `OF_NON_STOP_DESTINATION` (± `OF_NON_STOP_INTERMEDIATE`) | `OrderNonStopFlag::NoDestination` (script_order.cpp:527) |
| Full load all / full load any | `OF_FULL_LOAD` / `OF_FULL_LOAD_ANY` | `OrderLoadType::FullLoad` / `FullLoadAny` |
| No loading | `OF_NO_LOAD` | `OrderLoadType::NoLoad` |
| Unload all / transfer / no unloading | `OF_UNLOAD` / `OF_TRANSFER` / `OF_NO_UNLOAD` | `OrderUnloadType::*` |
| Go to depot / service at depot / stop in depot / nearest depot | `OF_*` depot flags with a depot tile, plus `OF_SERVICE_IF_NEEDED` / `OF_STOP_IN_DEPOT` / `OF_GOTO_NEAREST_DEPOT` | `OrderDepotTypeFlag::Service`, `OrderDepotActionFlag::Halt` / `NearestDepot` (script_order.cpp:487-492) |
| **Unbunch at depot** | **NOT EXPOSED** (script only sets Halt/NearestDepot) | `OrderDepotActionFlag::Unbunch` (order_type.h:120) |
| Platform stop location (near, middle, far) | `SetStopLocation` (new station orders default to far end, script_order.cpp:516) | `OrderStopLocation` |
| Refit at station/depot | `SetOrderRefit(cargo)` | CMD_ORDER_REFIT |
| Conditional orders: load %, reliability, max reliability, max speed, age, requires service, always, remaining lifetime | `OC_*` (all 8 engine variables) + `SetOrderCompareFunction/Value/JumpTo` | script_order.hpp:92-99 vs order_type.h:129-136 |
| Waypoint order | `AppendOrder(waypoint tile, flags)` → `MakeGoToWaypoint` | script_order.cpp:520-521 |
| Skip, move, delete, share, copy, unshare | `SkipToOrder`, `MoveOrder`, `RemoveOrder`, `ShareOrders`, `CopyOrders`, `UnshareOrders` | §7.2 |
| **Timetable** (wait/travel times, max speed per order, autofill, start date, "on time") | **NOT EXPOSED** (no read, no write) | CMD_CHANGE_TIMETABLE, BULK_CHANGE_TIMETABLE, SET_VEHICLE_ON_TIME, AUTOFILL_TIMETABLE, SET_TIMETABLE_START are not referenced in script api |

### 11.3 Groups, autoreplace, autorenew
| Human option | Script | Notes / evidence |
|---|---|---|
| Create, rename, delete, nest groups | `GSGroup.CreateGroup`, `SetName`, `DeleteGroup`, `SetParent` | script_group.cpp:37-85 |
| Move a vehicle to a group | `MoveVehicle` | :138. "Add shared vehicles" and "remove all" need loops |
| Group livery | `SetPrimaryColour`/`SetSecondaryColour` | :228,236 |
| Replace protection | `EnableAutoReplaceProtection` | :101 |
| Autoreplace engine X→Y (per group, incl. all and ungrouped) | `SetAutoReplace(group, old, new)`; `GROUP_ALL`/`GROUP_DEFAULT` allowed | :157-161. The **"replace only when old" toggle is NOT EXPOSED**: always passes `when_old=false` (autoreplace_cmd.h:19) |
| Stop autoreplace | `StopAutoReplace` | :177 |
| Wagon removal | `EnableWagonRemoval` (company setting `renew_keep_length`) | :146 |
| Autorenew on/off, months, money | `GSCompany.SetAutoRenewStatus/Months/Money` | script_company.cpp:319,335,351 |
| Depot "autoreplace all now" | **NOT EXPOSED** | CMD_DEPOT_MASS_AUTOREPLACE |

### 11.4 Refit
| Human option | Script | Notes |
|---|---|---|
| Build with refit | `BuildVehicleWithRefit(depot, engine, cargo)` | |
| Refit whole vehicle to cargo | `RefitVehicle(vehicle, cargo)` | Subtype fixed to 0, whole vehicle (script_vehicle.cpp:165). **NewGRF refit subtypes and refitting only part of a train are NOT EXPOSED** |
| Refit capacity preview | `GetRefitCapacity`, `GetBuildWithRefitCapacity` | QueryCost run (script_vehicle.cpp:156) |
| Refit as part of orders | `SetOrderRefit` | |

### 11.5 Town actions (`GSTown.PerformTownAction`, company mode, script_town.cpp:276)
All 8 local-authority actions are exposed: `TOWN_ACTION_ADVERTISE_SMALL/MEDIUM/LARGE`, `ROAD_REBUILD`, `BUILD_STATUE`, `FUND_BUILDINGS`, `BUY_RIGHTS`, `BRIBE` (script_town.hpp:35-77). Availability and state are readable via `IsActionAvailable`, `GetRating`, `GetDetailedRating`, `HasStatue`, `GetExclusiveRightsCompany/Duration`, `GetFundBuildingsDuration`, `GetRoadReworkDuration`.

### 11.6 Company settings (`company_settings.ini`, 9 settings)
| Setting | Script setter | Script getter |
|---|---|---|
| `engine_renew`, `engine_renew_months`, `engine_renew_money` | `SetAutoRenewStatus/Months/Money` | `GetAutoRenewStatus/Months/Money` |
| `renew_keep_length` (wagon removal) | `GSGroup.EnableWagonRemoval` | `GSGroup.HasWagonRemoval` |
| `vehicle.servint_ispercent`, `servint_trains`, `servint_roadveh`, `servint_ships`, `servint_aircraft` | **NOT EXPOSED** | **NOT EXPOSED** |

Also not exposed: per-vehicle service interval (CMD_CHANGE_SERVICE_INT) and the full manager face (only gender via `SetPresidentGender`).

### 11.7 What a human sees that scripts cannot query
Unchanged in 15.3; see §7.6. In short: timetables and lateness, service intervals, the "lost" flag, signal aspects and PBS reservations, sub-tile vehicle position and heading, cargo source and age, finance categories, rating breakdowns, most news events (engine available or preview, unprofitable, waiting in depot, aircraft range, autoreplaced, merger offers), and all rendered imagery.

---

## 12. (B) Engine patches for 15.3 — exact diffs, verified

**Files:** `docs/research/assets/patches/0001-gamespeed-console-command.patch`, `0002-paused-interval-unlimited-speed.patch`, `0003-gamescript-admin-json-32k.patch`.

**Verification performed:**
1. **Apply check.** `git apply --check` succeeded on a pristine `15.3` tag checkout (commit 14ec60f2), for each patch alone and for all three stacked. The patches touch disjoint files.
2. **Build.** All three applied with `git apply`, then a Release build with `-DOPTION_DEDICATED=ON`, Ninja, Apple clang on macOS. Result: 857/857 targets, `BUILD_EXIT=0`, no errors, no warnings in any touched file.
3. **Project integration tests.** I ran `tests/integration/test_engine_patches.py` with `RAILHEAD_OPENTTD` pointing at that binary, using the project venv. Four tests passed: `gamespeed` replies, a year (365 days) in under 15 s at speed 0, a 20,000-byte GS→admin message round-trips intact through `wire.py`, and a 40,000-byte message is refused with `ERR_SEND_FAILED` while the script survives. One test failed, `test_reseed_console_command_exists`; that `reseed` command belongs to the project's own patch set and is not part of this request.
4. **Console runtime check** (stdin console, 64×64 map, seed 42):

| Command | Logged result |
|---|---|
| `gamespeed` | `Game speed: 100` |
| `gamespeed 0` | `Game speed: 0`. The date ran from 1950-01-07 to 2032-10-17 in about 13 s wall time on an empty map, so this is an upper bound |
| `save t1`, `newgame 7`, `gamespeed` | `Game speed: 0` (kept across new game) |
| `load t1.sav`, `gamespeed` | `Game speed: 0` (kept across load) |

### 12.1 Patch 1 — `gamespeed` console command, sticky on dedicated servers
```diff
--- a/src/console_cmds.cpp
+++ b/src/console_cmds.cpp
@@ -30,6 +30,7 @@
 #include "window_func.h"
+#include "gfx_func.h"
@@ -817,6 +818,33 @@
+static bool ConGameSpeed(std::span<std::string_view> argv)
+{
+	if (argv.empty()) {
+		IConsolePrint(CC_HELP, "Set the game speed in percent of normal speed, 0 means unlimited. Usage: 'gamespeed [<percent>]'.");
+		IConsolePrint(CC_HELP, "A dedicated server keeps a non-default speed when a new game is started or a savegame is loaded; 'gamespeed 100' restores the default.");
+		return true;
+	}
+	if (argv.size() == 1) {
+		IConsolePrint(CC_DEFAULT, "Game speed: {}", _game_speed);
+		return true;
+	}
+	if (argv.size() != 2) return false;
+	auto speed = ParseInteger<uint16_t>(argv[1]);
+	if (!speed.has_value()) {
+		IConsolePrint(CC_ERROR, "'{}' is not a valid game speed.", argv[1]);
+		return true;
+	}
+	_game_speed = *speed;
+	_game_speed_sticky = (_game_speed != 100);
+	IConsolePrint(CC_DEFAULT, "Game speed: {}", _game_speed);
+	return true;
+}
@@ -2976,6 +3004,7 @@ void IConsoleStdLibRegister()
 	IConsole::CmdRegister("unpause",                 ConUnpauseGame,      ConHookServerOrNoNetwork);
+	IConsole::CmdRegister("gamespeed",               ConGameSpeed,        ConHookServerOrNoNetwork);
--- a/src/gfx.cpp
+++ b/src/gfx.cpp
 uint16_t _game_speed = 100; ///< Current game-speed; 100 is 1x, 0 is infinite.
+bool _game_speed_sticky = false; ///< Whether a dedicated server keeps #_game_speed when a game is (re)initialised; set by the 'gamespeed' console command.
--- a/src/gfx_func.h
+++ b/src/gfx_func.h
 extern uint16_t _game_speed;
+extern bool _game_speed_sticky;
--- a/src/misc.cpp
+++ b/src/misc.cpp
+#include "network/network.h"
 #include "network/network_func.h"
@@ void InitializeGame(...)
 	_pause_mode = {};
-	_game_speed = 100;
+	/* A dedicated server keeps the game speed set via the 'gamespeed' console command (e.g. over the admin port's rcon). */
+	if (!(_network_dedicated && _game_speed_sticky)) _game_speed = 100;
```
(Abridged hunks shown; the `.patch` file is the exact, applicable version.)
- **Who can use it:** `ConHookServerOrNoNetwork` (`console_cmds.cpp:221-228`) refuses network clients, so only the server or a single-player game can change speed. RCON reaches it because admin RCON runs `IConsoleCmdExec` on the server (`network_admin.cpp:495-496`).
- **Sticky condition:** the speed is kept only when `_network_dedicated` is true, which `dedicated_v.cpp:205` sets before any load, and the speed was set to something other than 100. `gamespeed 100` restores stock behaviour.
- **Output format:** matches the project's existing test contract, `Game speed: N`, for both query and set.

### 12.2 Patch 2 — 1 ms paused frames at unlimited speed
```diff
--- a/src/video/video_driver.hpp
+++ b/src/video/video_driver.hpp
@@ -305,8 +305,9 @@ protected:
 		TicToc::Tick("GameTick");
 
-		/* If we are paused, run on normal speed. */
-		if (_pause_mode.Any()) return std::chrono::milliseconds(MILLISECONDS_PER_TICK);
+		/* If we are paused, run on normal speed; with unlimited game speed keep the
+		 * paused frames short so network/admin traffic is handled with low latency. */
+		if (_pause_mode.Any()) return _game_speed == 0 ? std::chrono::milliseconds(1) : std::chrono::milliseconds(MILLISECONDS_PER_TICK);
```
- **Effect:** the dedicated loop sleeps until `next_game_tick` (`video_driver.cpp:173-185`). While paused at speed 0, each frame now lasts about 1 ms instead of 27 ms. That frame is where admin packets are read, commands executed and the GS runs (§1.1, §2.2). The CPU cost is small because the loop still sleeps.
- **Side effect:** a GUI single-player game paused while fast-forward is set to unlimited also spins paused frames at 1 kHz. Game state is unaffected; GS ticks and landscaping-limit refills per wall second rise.
- **Verification:** compiled, and the lockstep `step` test passed. The 1 ms interval itself was not timed.

### 12.3 Patch 3 — GS→admin JSON up to 32,747 bytes, refused cleanly above
```diff
--- a/src/network/core/config.h
+++ b/src/network/core/config.h
 static const uint NETWORK_GAMESCRIPT_JSON_LENGTH    = 9000;           ///< ...
+static const size_t NETWORK_GAMESCRIPT_JSON_SEND_LENGTH = TCP_MTU - 20; ///< The maximum length of a gamescript json string sent to admins, in bytes excluding '\0': TCP_MTU minus packet size (2), packet type (1), encryption MAC (16) and '\0' (1).
--- a/src/network/network_admin.cpp
+++ b/src/network/network_admin.cpp
 NetworkRecvStatus ServerNetworkAdminSocketHandler::SendGameScript(std::string_view json)
 {
-	auto p = std::make_unique<Packet>(this, ADMIN_PACKET_SERVER_GAMESCRIPT);
+	/* GameScript JSON may be larger than COMPAT_MTU; allow up to TCP_MTU. */
+	auto p = std::make_unique<Packet>(this, ADMIN_PACKET_SERVER_GAMESCRIPT, TCP_MTU);
+	if (!p->CanWriteToPacket(json.size() + 1)) {
+		Debug(net, 1, "[admin] GameScript JSON of {} bytes does not fit in a packet for '{}' ({}); dropped", json.size(), this->admin_name, this->admin_version);
+		return NETWORK_RECV_STATUS_OKAY;
+	}
 
 	p->Send_string(json);
--- a/src/script/api/script_admin.cpp
+++ b/src/script/api/script_admin.cpp
-	NetworkAdminGameScript(json.dump());
+	/* Replace invalid UTF-8 instead of letting nlohmann throw from inside a script call. */
+	std::string data = json.dump(-1, ' ', false, nlohmann::json::error_handler_t::replace);
+	if (data.size() > NETWORK_GAMESCRIPT_JSON_SEND_LENGTH) {
+		ScriptLog::Error(fmt::format("Send data is too large for the AdminPort ({} > {} bytes). No data sent.", data.size(), NETWORK_GAMESCRIPT_JSON_SEND_LENGTH));
+		sq_pushinteger(vm, 0);
+		return 1;
+	}
+
+	NetworkAdminGameScript(data);
--- a/src/script/api/script_admin.hpp
+++ b/src/script/api/script_admin.hpp
-	 * @note If the resulting JSON of your table is larger than 1450 bytes,
+	 * @note If the resulting JSON of your table is larger than 32747 bytes,
```
- **Why 32747:** a Packet's buffer starts with 2 size bytes, plus 16 MAC bytes if the admin session is encrypted, then 1 type byte (`packet.cpp:48-60`). `CanWriteToPacket` compares the total against the limit (`packet.cpp:90-93`). So the largest string that fits on an encrypted session is 32767 − 2 − 16 − 1 − 1 (NUL) = 32747. A single limit keeps the GS's success or failure independent of each admin's auth method. The extra `CanWriteToPacket` check in `SendGameScript` means a future caller can never hit the `Send_string` assert (`packet.cpp:174`).
- **The `error_handler_t::replace` change** is a small hardening beyond the request. The stock `json.dump()` uses nlohmann's strict mode, which throws `type_error` on invalid UTF-8 in a Squirrel string. What that exception would do to the server was not tested (UNVERIFIED).
- **Compatibility:** admin tools that assume server packets are at most 1460 bytes could break when a GS sends more. Any client that honours the 2-byte size field is fine.

### 12.4 Can Python admin clients receive packets larger than 1460 bytes? **Yes, up to 32767.**
- **Server side:** a packet of any size up to its limit is queued whole and written with partial-write handling. `SendPackets` loops `TransferOut` until the packet is drained (`tcp.cpp:76-113`); there is no 1460-byte segmentation. The size field is a 16-bit little-endian integer (`packet.cpp:71-72`). Values up to 32767 never set the high bit that is reserved for future larger packets (`config.h:31-45`), so the stock framing holds unchanged.
- **Client side:** the project's `src/railhead/wire.py` `FrameDecoder` accepts any size from 3 to 65535 (`MAX_PACKET_SIZE = 0xFFFF`). It was runtime-proven with the 20,000-byte echo (§12, verification item 3). Other third-party admin libraries were not checked (UNVERIFIED).
- **Caveat, other direction:** the server's *receive* limit is 32767 (`tcp.cpp:126`, `packet.cpp:267`). `wire.py`'s encoder allows up to 65535 outbound; a larger frame would make the server close the connection (risk 22).

### 12.5 Differences from the project's `engine/patches/` (written in parallel)
| Topic | `engine/patches` (project) | This deliverable |
|---|---|---|
| Speed across new game and load | Reset to 100 every time | Kept on dedicated servers when set via console or RCON |
| GS JSON limit | `TCP_MTU - 32`, check only in `ScriptAdmin::Send` | `TCP_MTU - 20` (exact for encrypted sessions), plus a `CanWriteToPacket` guard in `SendGameScript`, plus a UTF-8-safe `dump` |
| Extras | `reseed` console command | none |
| Paused interval | Same 1 ms logic | Same |

Adopting 12.1 makes the bridge's single `gamespeed 0` call survive the `load` used for forks.

---

## 13. (C) Map images from a dedicated server with the null blitter — **yes for `minimap` and `heightmap`, no for rendered views**

Runtime-verified on the patched 15.3 dedicated build. The patches don't touch screenshot code, so stock 15.3 behaves the same. The server ran `-D` with the null blitter forced (`openttd.cpp:540`), a 64×64 map, seed 42 and `[misc] screenshot_format = png`.

| Console command | Result logged | File produced | Why |
|---|---|---|---|
| `screenshot minimap rh_minimap` | `Screenshot successfully saved as 'rh_minimap.png'` | `screenshot/rh_minimap.png`, PNG 64×64, 8-bit RGB | Built from tile data, palette and recolour sprites; no blitter (`screenshot.cpp:473-503`) |
| `screenshot heightmap` | `Heightmap successfully saved as 'heightmap.png'. Highest peak is 4` | `screenshot/heightmap.png`, PNG 64×64, 8-bit palette | Greyscale from `TileHeight` (`screenshot.cpp:314-333`) |
| `screenshot giant rh_giant` | `Screenshot failed!` | none | Null blitter depth 0 (`blitter/null.hpp:18`); PNG writer accepts only 8 or 32 bpp (`screenshot_png.cpp:44`) |
| `screenshot normal rh_normal` | `Screenshot failed!` | none | same |
| `screenshot rh_viewport` | `Screenshot failed!` | none | same |

- **Dedicated-only builds cannot render at all.** With `-DOPTION_DEDICATED=ON`, the project's `engine/build.sh:28`, the 8bpp and 32bpp blitters are not compiled (`src/blitter/CMakeLists.txt:1-16`). Running `-D -b 32bpp-simple` on such a build hits the "failed to select blitter" error; on macOS that opens a modal Cocoa dialog and the process hangs (observed). Rendered screenshots would need a full, non-dedicated build run as `-D -b 32bpp-simple`; that remains UNVERIFIED.
- **Delivery:** the work is queued to the main loop (`screenshot.cpp:456-470`). Completion shows up only as a console line (`error_gui.cpp:314-330`), so subscribe to CONSOLE (type 6 @ 0x40) and wait for "successfully saved". Files land in `<cfg dir>/screenshot/`.
- **Use RCON, not stdin.** The dedicated stdin console reads through `std::cin` after a raw `select()` (`dedicated_v.cpp:149-192`). Several lines delivered at once got stuck until more input arrived (observed: the queued screenshot commands only ran after extra newlines).
- **What the minimap shows:** company colours, industries, towns, water and land, at one pixel per tile. There are no vehicles and rail is not distinguished from road (`smallmap_gui.cpp:580-607`). For model observations, combine it with GS tile scans.
