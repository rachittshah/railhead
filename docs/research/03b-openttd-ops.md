# 03b — Running OpenTTD headless: local macOS + Linux Docker (hands-on)

**Date:** 2026-10-16. **Host:** macOS 15 arm64, OpenTTD 15.2 at `/Applications/OpenTTD.app`, colima VM aarch64 (4 vCPU, 8 GB, Docker 28.3.3).
**Method:** every number below was measured in this session. Source references are to the 15.2 release tarball
(`openttd-15.2-source.tar.xz`, sha256 `d29ab617…3ff76`), shown as `src/<file>:<line>`. Things not executed are marked **NOT RUN**.
Companion report: `03-openttd-source.md` (engine internals). Where both cover a topic, they agree.

---

## 0. TL;DR

| Question | Answer (measured) |
|---|---|
| Local headless launch | `cd $RUN && HOME=$ISO_HOME openttd -D -x -c $RUN/openttd.cfg -s null -m null -G 12345` (config in §1.2). Admin port listening **0.27 s** after launch (64x64). |
| Admin auth in 15.2 | Plain `ADMIN_JOIN` works **only** with `allow_insecure_admin_login = true` (default false). Otherwise the server logs `not authorized` and closes the socket. Secure join = X25519 PAKE + encrypted session (needs Monocypher; not stdlib-doable). |
| Real-time pace (stock) | **1.998 s per game day** (74 ticks × 27 ms). One game year ≈ 12 min wall. |
| RCON round trip at 1x | **25–30 ms** (admin packets are handled once per game tick). Join 28–46 ms. |
| Fast-forward on a stock dedicated server | **Impossible.** No console command, no setting, no admin packet. `_game_speed` is GUI-only (`src/gfx.cpp:1858-1865`). |
| Fast-forward with a 30-line patch | **Works.** `gamespeed 0` over RCON: **5,634 days/s** on empty 64x64, **3,078 days/s** on a 64x64 save with 19 vehicles, 2,418 (256²), 799 (512²). RCON latency drops to **0.26 ms**. |
| Null driver (`-v null:ticks=N -g`) | Batch only (no network, no admin port). 54k–873k ticks/s depending on map size. Useful for offline rollouts from a save, not for an interactive env. |
| Save / load / newgame (RCON) | save 27–86 ms, load (to `WELCOME`) 72–89 ms, `newgame <seed>` 65–128 ms, for 64² to 512² maps. Admin connection survives load/newgame. |
| Reset determinism | Two `newgame 777` runs produce saves that differ **only** in the random 32-hex `savegame_id`. Everything else is byte-identical. |
| Recommended Docker route | **Build from source, dedicated-only** (`OPTION_DEDICATED=ON`), multi-stage on `debian:trixie-slim`. Cold build **192 s** (161 s compile) on 4 vCPU arm64. Image **190 MB** on disk, **45 MB** compressed. RSS per server **7–13 MiB**. |
| Why not binaries/apt | cdn.openttd.org has **no arm64 Linux build** (15.2 and 15.3: `linux-generic-amd64` only). Distros lag: Debian sid/forky and Ubuntu 26.04 ship **15.1**, trixie 14.1, noble 13.4. The apt image is 728 MB because it is a GUI build. |
| Python admin libraries | **pyOpenTTDAdmin 1.1.0** (2026-06-12) is the only maintained PyPI package with 15.x secure auth and prebuilt wheels. libottdadmin2 has secure auth but is git-only and CC BY-NC-SA. |
| `~/ottd-claude/game_client.py` approach | **Not viable for Railhead.** It needs exact version coupling, gets no command feedback, re-downloads the map on every reset, and would be lag-kicked under fast-forward (§5.3). |

---

## 1. Local headless run (macOS)

### 1.1 How OpenTTD picks its directories (and how to isolate it)

| Mechanism | Effect | Evidence |
|---|---|---|
| `-c /dir/openttd.cfg` | `/dir` becomes the working-dir search path, the **config dir** and the **personal dir**. `save/`, `baseset/`, `game/`, `ai/`, `content_download/`, `secrets.cfg`, `private.cfg` all resolve under `/dir`. | `src/fileio.cpp:819-830`, `:906-908`, `:946`, `:965`; observed log `…/run/ found as config directory` / `personal directory` |
| macOS personal dir | `$HOME/Documents/OpenTTD` is still a **read** search path even with `-c`. Override `HOME` to keep the user's folder out. | observed; `~/Documents/OpenTTD` had no files newer than session start |
| Linux | XDG: `$XDG_CONFIG_HOME/openttd` (cfg) and `$XDG_DATA_HOME/openttd` (data), unless `-c` is given. | `src/fileio.cpp` `USE_XDG` branch |
| `-x` | Never write the config back. Without it, SIGTERM rewrote `openttd.cfg` (460 lines) and moved secrets into `secrets.cfg`. | observed |
| **No `[version]` section** | The ini version is 0, so secret/private settings (`admin_password`, `server_name`…) are read **from `openttd.cfg` itself**, and `secrets.cfg` is ignored. With the password only in `secrets.cfg`, `admin_password` read as `''` and the admin port **did not listen**. | `src/settings.cpp:1391-1395`; observed |
| Admin port | It listens only if `admin_password` or `admin_authorized_keys` is non-empty. | `src/network/network.cpp:908-910` |
| Seed | `generation_seed` is `NotInConfig`. Use `-G <seed>` or console `newgame <seed>`. | `src/table/settings/world_settings.ini:200-208` |
| **`-X` on macOS hangs** | `-X` drops the app-bundle Resources path, so startup hits a fatal error. On macOS a fatal error opens a **modal NSAlert** even under `-v null`. The log shows `Could not change to foreground application. Error -50` and the process blocks forever. Linux prints `Error: …` to stderr and exits. | `src/os/macosx/macos.mm:136-144` vs `src/os/unix/unix.cpp:193-199`; observed and killed |

**Rule:** generate one self-contained `openttd.cfg` per env with no `[version]` section, run with `-x`, set `HOME` to an isolated dir on macOS, and wrap every launch in a timeout.

### 1.2 Exact config and commands (verified)

`$RUN/openttd.cfg`:

```ini
; Single-file config. No [version] section => secrets/private settings are read from THIS file.
[gui]
autosave_interval = 0
threaded_saves = false
[difficulty]
max_no_competitors = 0
[game_creation]
map_x = 6            ; 2^6 = 64
map_y = 6
starting_year = 1950
land_generator = 1
[network]
server_port = 39790
server_admin_port = 39770
server_game_type = local
admin_password = railhead
allow_insecure_admin_login = true
min_active_clients = 0
pause_on_join = false
server_name = railhead-probe
```

Data and launch:

```bash
RUN=$SCRATCH/ottd/run; ISO_HOME=$SCRATCH/ottd/home
mkdir -p $RUN/baseset $RUN/game/library $ISO_HOME
cp ~/Documents/OpenTTD/content_download/baseset/4f474658-OpenGFX-8.0.tar $RUN/baseset/     # read-only copy
cp ~/Documents/OpenTTD/content_download/game/library/*.tar         $RUN/game/library/      # GS libs for later
cd $RUN
: > cmds.txt
# stdin console is optional; feeding it from a growing file lets other shells append commands
tail -n +1 -f cmds.txt | HOME=$ISO_HOME /Applications/OpenTTD.app/Contents/MacOS/openttd \
    -D -x -c $RUN/openttd.cfg -s null -m null -G 12345 > server.log 2>&1 &
nc -z 127.0.0.1 39770 && echo admin up        # true after ~0.27 s
```

Server log excerpt:

```
dbg: [net:3] Starting dedicated server, version 15.2
dbg: [net:3] Listening on 0.0.0.0:39790 (IPv4)
dbg: [net:3] Listening on 0.0.0.0:39770 (IPv4)
dbg: [net:3] Map generated, starting game
```

`-s null -m null` is belt-and-braces. The `-D` path never opened a window or dock icon.

### 1.3 Measurements (64x64 unless noted, admin RCON from Python on loopback)

| Operation | Result |
|---|---|
| Game pace at 1x | 10 days per 20 s on stdin `getdate`, and 6 days per 11.99 s on admin `DATE` daily push: **1.998 s/day** |
| Admin join (TCP connect to `WELCOME`) | 28–46 ms |
| RCON `getdate`, `companies`, `pause`, … | 25–30 ms each (one tick) |
| `save probe_a` | 27–30 ms, 15 KB file |
| `load probe_a` | `RCON_END` 29 ms, then `NEWGAME` + `WELCOME` at **80 ms** |
| `newgame 12345` | `WELCOME` after 65–81 ms |

Map-size scaling (`setting_newgame map_x N` → `newgame 4242` → `pause` → `save` → `ls` → `load`):

| Map | newgame | save | load | .sav size |
|---|---|---|---|---|
| 64x64 | 78 ms | 34 ms | 89 ms | 14 KB |
| 128x128 | 78 ms | 38 ms | 72 ms | 29 KB |
| 256x256 | 102 ms | 48 ms | 74 ms | 93 KB |
| 512x512 | 128 ms | 86 ms | 88 ms | 339 KB |

**Determinism check:** run `setting pause_on_newgame true`, then `newgame 777` + `save det_d1`, then `newgame 777` + `save det_d2`. The lzma-decompressed saves are 89,958 bytes each with 31 differing bytes. All of them sit in the `DATE` chunk's `id` field (the random `savegame_id`, `C9750A4E…` vs `715A9FD9…`). The map, RNG state and tick counter are identical.

### 1.4 Console and RCON gotchas found

1. **The stdin console can stall.** `-D` reads stdin with `std::getline(std::cin)` after a `select()` (`src/video/dedicated_v.cpp` `DedicatedHandleKeyInput`). Several lines written at once sit in the stream buffer until the next write wakes `select()`. Observed: four commands produced one output until more lines arrived. Write one command at a time, or better, use admin RCON.
2. **`save` does not refresh the console save list.** `load <name>` right after `save <name>` answers `'<name>' cannot be found`. `ConLoad` only validates a cached list, and only `ls`/`list_saves`/`rm` refresh it (`src/console_cmds.cpp` `ConSave`/`ConLoad`/`ConListFiles`). **Always send `ls` before `load`.**
3. **`pause_on_newgame` works at runtime.** `setting pause_on_newgame true` makes every `newgame`/reset start paused, which gives a clean RL reset.
4. A `load` re-sends `NEWGAME` then `WELCOME` to connected admins, and the session stays up. Normal game clients are disconnected instead.
5. **Console commands in the 15.2 release build:** `alias authorized_key ban banlist cd clear client_name clients companies connect content debug_level dump_info echo echoc exec exit font fps fps_wnd gamelog getdate getseed getsysdate help info_cmd join kick list_ai list_ai_libs list_aliases list_cmds list_dirs list_game list_game_libs list_heightmaps list_saves list_scenarios list_settings load load_heightmap load_save load_scenario ls move newgame part pause pwd quit rcon reconnect reload reload_ai rescan_ai rescan_game rescan_newgrf reset_company reset_enginepool resetengines restart return rm save saveconfig say say_client say_company schedule screenshot script scrollto server_info setting setting_newgame start_ai status stop_ai unban unpause zoomto`. `gamespeed` → `Command 'gamespeed' not found.`

---

## 2. Fast simulation

### 2.1 Stock 15.2: the dedicated server cannot fast-forward

The dedicated main loop is `Tick(); SleepTillNextTick();` (`src/video/dedicated_v.cpp` `MainLoop`). The tick interval is `MILLISECONDS_PER_TICK*1000*100/_game_speed` µs, and `_game_speed == 0` means "as fast as possible" (`src/video/video_driver.hpp:311-313`). So the server **would** run fast, but nothing reachable from the console or admin port changes `_game_speed`. Only `ChangeGameSpeed()` (`src/gfx.cpp:1858-1865`), called from the GUI toolbar or hotkey, changes it. `gui.fast_forward_speed_limit` (2500) only caps that GUI toggle. `InitializeGame()` also resets `_game_speed = 100` on every new game or load (`src/misc.cpp:101`).

### 2.2 Patched dedicated: a `gamespeed` console command (built and measured)

The patch is 30 lines in one file (`src/console_cmds.cpp`). It is inlined in `assets/Dockerfile.openttd-probe`, applies cleanly to 15.2, and a dry run applied it cleanly to 15.3:

```cpp
static bool ConGameSpeed(std::span<std::string_view> argv)
{
	if (argv.empty()) { IConsolePrint(CC_HELP, "...Usage: 'gamespeed [<percentage>]'."); return true; }
	if (argv.size() > 2) return false;
	if (argv.size() == 2) {
		auto speed = ParseInteger<uint16_t>(argv[1]);
		if (!speed.has_value()) { IConsolePrint(CC_ERROR, "'{}' is not a valid game speed.", argv[1]); return true; }
		_game_speed = *speed;
	}
	IConsolePrint(CC_DEFAULT, "Game speed: {}%", _game_speed);
	return true;
}
// registered next to getdate:
IConsole::CmdRegister("gamespeed", ConGameSpeed, ConHookServerOrNoNetwork);
```

Measured in the patched container (arm64, admin `DATE` daily push, 3–5 s windows):

| Setting / world | Days per second | Ticks per second |
|---|---|---|
| `gamespeed 100` (64², empty) | 0.5 | 37 |
| `gamespeed 1000` | 5.0 | 370 |
| `gamespeed 5000` | 24.9 | 1.8k |
| `gamespeed 0`, 64² empty (46 years in 3 s) | **5,634** | ~417k |
| `gamespeed 0`, 64² "Lintfield" save with 19 road vehicles (42 years in 5 s, money 0.55M → 2.7M) | **3,078** | ~228k |
| `gamespeed 0`, 256² fresh map | 2,418 | ~179k |
| `gamespeed 0`, 512² fresh map | 799 | ~59k |

Other observations:

- RCON `getdate` latency at unlimited speed: **median 0.26 ms, max 0.37 ms**, against 26 ms at 1x.
- `pause` still freezes the world, with zero days advancing in 1 s.
- After `newgame`, `gamespeed` reads `100%` again, so the client must **re-issue `gamespeed 0` after every reset or load**.
- Container RSS stayed at 7.4–13 MiB across all these runs, so dozens of envs per host is realistic.

The unlimited-speed rates are fast enough that stepping precision becomes the issue. The workable pattern is `unpause`, then wait for the target `DATE`, then `pause`. At about 5k days/s, a 0.3 ms RCON round trip overshoots by roughly 1–2 days. For exact stepping, have the GameScript pause at a target date (**NOT RUN** here). Alternatively use a moderate speed such as 5000%.

### 2.3 Null video driver: offline batch only

`-v null:ticks=N` runs N `GameLoop()` iterations with no sleeping, then exits. With `gui.autosave_on_exit = true` it writes `save/autosave/exit.sav` first (`src/video/null_v.cpp` `MainLoop`). It never starts networking, so there is no admin port or RCON. It also cannot advance a save that was stored **paused**: two such saves showed zero progress. Verified commands:

```bash
# new game from config (+ seed), 200k ticks, writes save/autosave/exit.sav when autosave_on_exit = true
HOME=$ISO_HOME openttd -v null:ticks=200000 -s null -m null -x -c $DIR/openttd.cfg -G 12345 -g
# continue an (unpaused) save
HOME=$ISO_HOME openttd -v null:ticks=100000 -s null -m null -x -c $DIR/openttd.cfg -g $DIR/save/x.sav
```

| World | macOS native | Linux container |
|---|---|---|
| 64² new game, 200k ticks (1950 → 1957-05-26) | 0.25 s, 811k ticks/s | 0.23 s, 873k ticks/s |
| 256² new game, 100k ticks | 0.41 s, 245k ticks/s | 0.44 s, 229k ticks/s |
| 512² new game, 50k ticks | 0.81 s, 62k ticks/s | 0.93 s, 54k ticks/s |
| Lintfield save (19 RVs), 100k ticks (2121-12-17 → 2125-08-29) | 0.41 s, 243k ticks/s | NOT RUN |

Startup overhead is about 0.09 s per process. The engine runs at roughly 5,000–20,000x real time on small maps. Under real-time pacing, the bottleneck is the 27 ms/tick sleep, not the simulation.

---

## 3. Admin port in 15.2: auth and a minimal client

- Admin protocol version is **3**.
- The `SERVER_PROTOCOL` update-frequency bitmasks on 15.2 are `{DATE:0x3f, CLIENT_INFO:0x41, COMPANY_INFO:0x41, COMPANY_ECONOMY:0x3d, COMPANY_STATS:0x3d, CHAT:0x40, CONSOLE:0x40, CMD_NAMES:0x01, CMD_LOGGING:0x40, GAMESCRIPT:0x40}`.
- `AdminUpdateFrequency` is now an `enum class` bitset: Poll=0x01, Daily=0x02, …, Automatic=0x40.
- **Plain `ADMIN_JOIN` (type 0)** sends password, name and version. It succeeds only if `network.allow_insecure_admin_login = true` (`src/network/network_admin.cpp:629`).
- With the default `false`, the server logs `The admin '' () made an error and has been disconnected: 'not authorized'`. The Python side only saw the socket close; no `SERVER_ERROR` packet was readable first. The setting can be flipped at runtime with `setting network.allow_insecure_admin_login true|false`. Both directions were verified.
- **Secure join** follows this sequence:
  1. The client sends `ADMIN_JOIN_SECURE` (9).
  2. The server replies `SERVER_AUTH_REQUEST` (128), and the client answers with `ADMIN_AUTH_RESPONSE` (10).
  3. The server sends `SERVER_ENABLE_ENCRYPTION` (129), and every later packet is AEAD-encrypted.
  4. Methods are X25519 PAKE with a password, or authorized keys. The crypto is Monocypher's incremental AEAD with key ratcheting, which Python's stdlib cannot do.
- **Recommendation for Railhead:** use plain join with `allow_insecure_admin_login = true`. Keep the admin port container-internal, or publish it on `127.0.0.1` only. Switch to pyOpenTTDAdmin if the port must cross a network.

**Throwaway client** (stdlib only, about 200 lines): `$SCRATCH/client/probe_admin.py`, with scenarios `basic | speed | saveload | newgame | scale | determinism | disable_insecure | insecure_off`. Sample output:

```
joined in 37.5 ms: {'admin_protocol': 3, ..., 'server_name': 'railhead-probe', 'revision': '15.2', 'dedicated': True,
                    'seed': 12345, 'landscape': 0, 'start_date': 712223, 'map_x': 64, 'map_y': 64}
rcon 'getdate' (26.9 ms): ['Date: 1950-02-05']
rcon 'getseed' (27.0 ms): ['Generation Seed: 12345']
rcon save probe_a: 29.8 ms -> ['Saving map...', "Map successfully saved to 'probe_a.sav'."]
```

**The user's `~/ottd-claude/src/ottd_claude/admin_client.py`** uses plain `ADMIN_JOIN`. It works on 15.2 only because the user's own `openttd.cfg` sets `allow_insecure_admin_login = true`. Its `GameState.date_to_str` uses 365-day years and 30-day months, so displayed dates drift. Correct conversion: date = days since 0000-01-01 in the proleptic Gregorian calendar, so `date(1,1,1) + timedelta(days=d-366)`.

---

## 4. Linux / Docker

### 4.1 Distribution options (checked 2026-10-16)

| Route | Version | Arches | Build time (arm64, 4 vCPU) | Image | Notes |
|---|---|---|---|---|---|
| cdn.openttd.org `linux-generic` tarball | 15.2, 15.3 | **amd64 only** | download only | 23 MB tar.xz plus base | GUI build with bundled `lib/`. On trixie-slim it fails with `libgomp.so.1` / `libglib-2.0.so.0` not found unless run with `LD_LIBRARY_PATH=$PWD/lib`. Then `openttd -h` works under amd64 emulation. |
| Debian sid / forky `apt install openttd openttd-opengfx` | **15.1** | amd64, arm64 | **55 s** | **728 MB** (186 MB compressed) | GUI build (SDL, OpenGL). OpenGFX package is 7.1. Version differs from the Mac's 15.2. |
| Ubuntu 26.04 resolute | 15.1 | both | NOT RUN | — | same package |
| Debian trixie / Ubuntu questing / noble | 14.1 / 14.1 / 13.4 | both | — | — | too old |
| **Source, dedicated-only (recommended)** | exact 15.2 (or 15.3) | both, native | **192 s cold** (161 s compile); 173 s with cached deps (155 s compile) | **190 MB** (45 MB compressed) | `/opt/openttd` is 34 MB. Video drivers are `dedicated` and `null` only. Patchable. |

OpenGFX URL: `https://cdn.openttd.org/opengfx-releases/8.0/opengfx-8.0-all.zip`. It contains `opengfx-8.0.tar`, which you drop into `baseset/`. `opengfx-releases/latest.yaml` reports 8.0, dated 2026-01-04. Source URL: `https://cdn.openttd.org/openttd-releases/15.2/openttd-15.2-source.tar.xz`, sha256 `d29ab617b7c0faa56ec4f8f13663c690a1b85e9212dd01717cc214c720d3ff76`, from `manifest.yaml`.

**Version note:** `openttd-releases/latest.yaml` lists **15.3** (2026-04-04) as stable and 16.0-beta4 as testing. 15.3 fixes a train-crash desync (#15338) and a script `IsBuildableRectangle` bug (#15357). The 15.3 source sha256 is `5ea21eea7d59c78a42071924ac18c6bc0116088f2e96b14cfee9369175973be7`. Pin **one** version on the Mac and in the containers, because game-client joins need an exact revision match and saves should round-trip. 15.3 everywhere is the better pin: the Dockerfile only needs `OPENTTD_VERSION` and `OPENTTD_SHA256` build args changed (**NOT RUN**).

### 4.2 Dockerfile

The working file is at `docs/research/assets/Dockerfile.openttd-probe` and is self-contained, with the patch inlined via a BuildKit heredoc. It builds with no context besides itself. Key choices:

- **Multi-stage build.** The build stage is `debian:trixie-slim` with `build-essential cmake ninja-build zlib1g-dev liblzma-dev liblzo2-dev`. The runtime stage is `debian:trixie-slim` with `zlib1g liblzma5 liblzo2-2 netcat-openbsd`.
- **cmake flags:** `-DOPTION_DEDICATED=ON -DOPTION_INSTALL_FHS=OFF -DCMAKE_INSTALL_PREFIX=/opt/openttd`, then `cmake --install --strip`. This drops the SDL, fontconfig, freetype and ICU dependencies and puts the binary next to `lang/`, `baseset/`, `ai/` and `game/`.
- **`ARG GAMESPEED_PATCH=1`.** Set it to 0 for a vanilla build.
- **Runtime user and health.** It runs as non-root `ottd` (uid 1000). A `HEALTHCHECK` runs `nc -z 127.0.0.1 3977`, and the admin port only opens when auth is configured.

### 4.3 Running it (verified)

```bash
# no build context needed (patch is inlined); verified: cached rebuild in 2 s, image healthy
docker build -t railhead/openttd:15.2 - < docs/research/assets/Dockerfile.openttd-probe
docker run -d --name ottd -p 127.0.0.1:39771:3977 -e CFG="$(cat openttd.container.cfg)" \
  --entrypoint sh railhead/openttd:15.2 \
  -c 'mkdir -p ~/r && printf "%s\n" "$CFG" > ~/r/openttd.cfg && exec /opt/openttd/openttd -D -x -c ~/r/openttd.cfg -s null -m null -G 12345'
python3 probe_admin.py 127.0.0.1 39771 railhead basic
```

`openttd.container.cfg` is the §1.2 file with `server_port = 3979` and `server_admin_port = 3977`.

- **Startup:** `docker run` to the first admin `SERVER_PROTOCOL` reply took **0.33 s**.
- **Asset image end-to-end:** built from the asset file, the container reported `health=healthy` and ran as user `ottd`. `gamespeed 0` gave 5,564 days/s, with RCON latency at a 0.26 ms median.
- **Config injection:** the config goes in through an env var because colima only shares `$HOME` with the VM. A bind mount from `/private/tmp/...` arrives empty, as `04-harbor.md` also found. Mounting from under `$HOME` works.
- **Multi-arch:** build natively on each arch and join them with `docker buildx imagetools create`, for example a GitHub `ubuntu-24.04-arm` runner plus an amd64 runner. QEMU emulation of a 900-unit C++ build would be far slower (**NOT RUN**). The amd64 path is the same Dockerfile, unmodified.

---

## 5. Prior art

### 5.1 Python admin-port libraries

These were checked via the PyPI JSON API and the GitHub REST API on 2026-10-16, with code read but not executed.

| Library | Latest | 15.x secure auth | Packaging | Status |
|---|---|---|---|---|
| **pyOpenTTDAdmin** (`liki-mc/pyOpenTTDAdmin`, PyPI `pyopenttdadmin`) | 1.1.0, 2026-06-12 (first 2024-05) | **Yes**: password PAKE and secret-key, encrypted session (`pyopenttdadmin/auth.py`, bundled Monocypher C extension); plain login via `secure=False` | wheels for cp39–cp314, manylinux/musllinux aarch64 and x86_64, macOS arm64/x86_64, Windows; no runtime deps; sync plus `aiopyopenttdadmin` | active; 7 stars |
| **libottdadmin2** (`xaroth/libottdadmin2`) | 0.0.4a1 in `setup.py`; **not on PyPI** under that name | **Yes**: secure-auth commits 2024-03-30, merged PR #17 from `rubidium42/pubkey` 2024-07-02; uses `pymonocypher` (4.0.3.4 on PyPI, 2026-10-08) | git install | last push 2024-07-02. **License header: CC BY-NC-SA 3.0 (non-commercial)** |
| python3-openttd (`horazont`) | 0.2.0, 2021-08-15 | No (pre-14) | PyPI | stale; plain-join only |
| `openttd-protocol` (`OpenTTD/py-protocol`, official) | 1.7.1 (PyPI 2024-01; repo pushed 2026-01-01) | n/a | PyPI | only game/content/coordinator/STUN/TURN protocols, **no admin protocol** |
| gopenttd (Go), suds and soap (Supybot), Joan (Java, link in `docs/admin_network.md` is dead) | — | — | — | not Python, or stale |

**Recommendation:** Railhead's admin surface is small: join, RCON, poll, update-frequency and GameScript JSON. It is about 200 lines of stdlib Python, as the probe shows, using plain join on a private network. Use pyOpenTTDAdmin if encrypted auth is ever required.

### 5.2 RL / LLM projects on OpenTTD

**NOT SURVEYED.** The session's shared web-search budget (200 calls across all agents) was exhausted before this step, and the tool forbids working around it. The only GitHub or PyPI lookups done were direct metadata fetches of known library names. The one OpenTTD-plus-LLM project verified locally is the user's own `~/ottd-claude`: Claude advisor over the admin port, a GameScript for signs, goals and story pages, and a game-port client for building. A proper sweep of GitHub, arXiv and the tt-forums for OpenTTD RL or LLM agents still needs doing.

### 5.3 `~/ottd-claude` `game_client.py`: joining as a network game client

What it does (1,215 lines, read in full where relevant):

- **Join and crypto.** It joins the game port as a regular client with hard-coded `OPENTTD_REVISION = "15.2"` and NewGRF version `0x1F286D64`. It re-implements X25519 key exchange and Monocypher-style ratcheting XChaCha20-Poly1305 using PyNaCl plus a pure-Python HChaCha20.
- **Map and frames.** It discards the map download (`SERVER_MAP_DATA: pass`) and ACKs frames from a heartbeat task.
- **Commands.** It sends `CLIENT_COMMAND` packets with a hand-copied `Commands` enum (`CMD_BUILD_RAIL_LONG = 0`, …) and hand-serialized argument tuples.

Assessment against a GameScript bridge:

| Concern | Game-client approach | GameScript bridge |
|---|---|---|
| Version coupling | The revision string must match exactly. Command IDs and argument layouts are internal C++ enums and tuples that change between releases (`CMD_NAMES` is documented as "not stable"). | The GS API is versioned, and `compat_*.nut` shims keep old scripts working (`Contents/Resources/game/compat_1.2…14.nut`). |
| Action feedback | **None.** Commands run on every peer at a scheduled frame, and errors only surface in the issuing client's own simulation. A non-simulating ghost client cannot tell success from failure. | Synchronous return values plus `GSError.GetLastErrorString()`. |
| Observations | None, because the map is discarded. It still needs admin or GS for state. | Full GS read API (tiles, stations, vehicles, cargo). |
| Reset cost | `newgame`/`load` disconnects game clients. Every reset means reconnect, re-authenticate and re-download the full map. | Admin sessions survive `newgame`/`load` (verified). The GS restarts with the game. |
| Fast-forward | **Breaks.** The server drops clients more than `max_lag_time` ticks behind (default 500, max 32,000; `src/network/network_server.cpp:1701-1710`). At 60k–400k ticks/s that is 0.1–0.5 s of slack at best. | Unaffected, since the GS runs inside the server tick. |
| Company control | Occupies a client slot and company. | `GSCompanyMode(cid)` acts for any company (see `03-openttd-source.md` §2.1). |
| Throughput limit | Network command queue (`commands_per_frame`) | GS opcode budget per tick (`script_max_opcode_till_suspend = 10000`), and the GS↔admin JSON size caps (see `03-openttd-source.md`) |

**Verdict:** the game-client path is a fragile, version-locked, blind actuator, and it is incompatible with fast-forward. Use **Python ⇄ admin port ⇄ GameScript**. Lifecycle goes over admin RCON (`newgame`, `ls` + `load`, `save`, `pause`, `gamespeed`). Actions and observations go over `ADMIN_GAMESCRIPT` / `SERVER_GAMESCRIPT` JSON.

---

## 6. Recommended ops recipe for Railhead

1. **Image:** use the source build from `assets/Dockerfile.openttd-probe`, pinned to one version that also runs on the Mac (15.3 preferred). Keep the `gamespeed` patch.
2. **One server per env.** Use a unique `-c` dir and ports, `-x`, `-s null -m null`, and plain admin join on a private network. RSS is 7–13 MiB per server.
3. **Bootstrap once per server:** `setting pause_on_newgame true`.
4. **Reset:** send `newgame <seed>`, or `ls` then `load <snapshot>`. Wait for admin `WELCOME`, which takes 65–130 ms. Then send `gamespeed 0`, or another chosen speed, because each reset puts it back to 100.
5. **Step:** `unpause`, wait for the target `DATE` push (or a GS signal), then `pause`.
6. **Snapshot or fork:** `save <name>` takes 27–86 ms. Run `ls` before any `load`.
7. **Safety:** put a timeout on every process launch, which matters most on macOS (§1.1 `-X` hang), and never use `-X` on macOS.

### Files produced (scratchpad, not committed)

- `$SCRATCH/client/probe_admin.py` — stdlib admin client with all scenarios
- `$SCRATCH/client/probe_ffwd.py`, `probe_ffwd_save.py`, `rcon_cli.py` — fast-forward and RCON helpers
- `$SCRATCH/ottd/launch_dedicated.sh`, `$SCRATCH/ottd/openttd.min.cfg`, `$SCRATCH/ottd/openttd.full-15.2.cfg` (the full 460-line 15.2 settings dump)
- `$SCRATCH/docker/final/Dockerfile` (= asset), `$SCRATCH/docker/gamespeed.patch`

`$SCRATCH` = `/private/tmp/claude-501/-Users-rachitt/a8c1f8a7-1e71-4e78-bc25-15b2761b9b4f/scratchpad`.
