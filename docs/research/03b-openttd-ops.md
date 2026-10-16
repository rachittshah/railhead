# 03b — Running OpenTTD headless: macOS, Linux Docker, fast-forward, visual channel (hands-on)

**Date:** 2026-10-16. **Host:** macOS 15 arm64, colima VM aarch64 (4 vCPU, 8 GB, Docker 28.3.3).
**Binaries:**

- The local macOS runs used the installed app, **15.2** (`/Applications/OpenTTD.app`).
- The Docker images are built from tag **15.3** (`14ec60f248547d4d062a1160f0fc26d742319888`), the latest stable 15.x per `git ls-remote`. Tags 16.0-beta1..4 also exist.

**Method:** every number below was measured in this session. Source references are `src/<file>:<line>` in the 15.3 tarball. The files cited are identical in 15.2 unless noted. Items marked **NOT RUN** were read or inferred but not executed. Companion report: `03-openttd-source.md`.

---

## 0. TL;DR

| Question | Answer (measured) |
|---|---|
| macOS app as a headless server | **Yes, `-D` runs fine.** `cd $RUN && HOME=$ISO_HOME /Applications/OpenTTD.app/Contents/MacOS/openttd -D -x -c $RUN/openttd.cfg -G 12345` gives an admin port **0.27 s** after launch. No window or dock icon appears. |
| Admin auth (15.x) | Plain `ADMIN_JOIN` works only with `allow_insecure_admin_login = true` (default false). Otherwise the socket is closed and the server logs "not authorized". Secure join (X25519 PAKE then an encrypted session) needs Monocypher. |
| Stock pace | **1.998 s per game day** at 1x. RCON round trip 25–30 ms (one tick). |
| Fast-forward, stock | **Impossible on a dedicated server.** No console command, setting or admin packet changes `_game_speed`. |
| Fast-forward, patched (Railhead `gamespeed`) | 15.3 image, empty 64x64 map: `gamespeed 1000` gives 5.0 days/s, `5000` gives 24.9, `0` gives **5,322 days/s**. Bigger maps (15.2 build): 2,418 days/s on 256², 799 on 512². RCON at unlimited speed: **0.26 ms**. |
| Paused-poll part of the patch | While paused at gamespeed 0, RCON drops from **27 ms to 1.7 ms**, and the GameScript loop rises from **37 to ~1,090 iterations/s**, with the world frozen. |
| Null driver (`-v null:ticks=N`) | Offline batch only, with no admin port. macOS: 811k / 245k / 62k ticks/s on 64² / 256² / 512² fresh maps. Linux container: 873k / 229k / 54k. Vehicle-heavy worlds were **not measured**. |
| Docker route | **Patched 15.3 source build, dedicated-only** (`-DOPTION_DEDICATED=ON`, Release, Ninja), multi-stage `debian:trixie-slim`. Cold no-cache build on arm64 took **182 s** (163 s compile). Image is **191 MB** on disk, **45.1 MB** compressed. Server RSS is **7–9 MiB**. Asset: `assets/Dockerfile.openttd-probe`. |
| Visual channel | **Yes, three ways, all verified.** (1) The dedicated build renders `screenshot minimap` and `heightmap` over RCON. (2) A GUI build run as `-D -b 32bpp-optimized` also renders `screenshot giant`, the whole map isometric. (3) A **GUI client under Xvfb in Docker** joins the dedicated server, plays a company, gives 1280x720 frames via `scrot`, and takes mouse/keyboard input via `xdotool` (a road was built this way). |
| GUI client under fast-forward | It keeps up to about 200x (96 days/s on 128²). At 400x and above it is **dropped** as "more than 501 ticks behind" (`max_lag_time`). |
| Building while paused (any player) | Refused at the default `construction.command_pause_level = 1` with "Not allowed while paused". Works with `= 3`, which must be set **in the config** because `setting` refuses it during a network game. |
| Closest prior art for direct play | **nttd** (deepsaia, Apache-2.0, 2026): OpenTTD 15.3 dedicated server, admin port and a 5.7k-line GameScript bridge using `GSCompanyMode`, 76 "human-parity" actions, REST/MCP/Gym surfaces, macOS-only, 1x pacing. No computer-use or GUI agent for OpenTTD was found. |

---

## 1. Local headless run (macOS app 15.2)

### 1.1 Directory resolution and isolation

| Mechanism | Effect | Evidence |
|---|---|---|
| `-c /dir/openttd.cfg` | `/dir` becomes the working-dir search path, the **config dir** and the **personal dir**. `save/`, `baseset/`, `game/`, `ai/`, `screenshot/`, `content_download/`, `secrets.cfg` and `private.cfg` all resolve under `/dir`. | `src/fileio.cpp:819-830`, `:906-908`, `:965`; log line `…/run/ found as config directory` |
| macOS personal dir | `$HOME/Documents/OpenTTD` stays a **read** search path even with `-c`. Override `HOME` for full isolation. | observed |
| Linux | `$XDG_CONFIG_HOME/openttd` and `$XDG_DATA_HOME/openttd`, unless `-c` is given | `src/fileio.cpp` `USE_XDG` |
| `-x` | Never write the config back. Without it, SIGTERM rewrote `openttd.cfg` (460 lines) and split secrets into `secrets.cfg`. | observed |
| **No `[version]` section** | Secret and private settings (`admin_password`, `server_name`, `client_name`) are read **from `openttd.cfg` itself**, and `secrets.cfg` is ignored. With the password only in `secrets.cfg`, it read as `''` and the admin port **did not listen**. | `src/settings.cpp:1391-1395`; observed |
| Admin port | Listens only if `admin_password` or `admin_authorized_keys` is set | `src/network/network.cpp:908-910` |
| Seed | `NotInConfig`, so use `-G <seed>` or `newgame <seed>` | `src/table/settings/world_settings.ini:200-208` |
| `-D` | Forces `sound=null`, `music=null`, `video=dedicated`, `blitter=null`. A later `-b <blitter>` overrides the blitter (used in §5). | `src/openttd.cpp:535-541` |
| **`-X` on macOS hangs** | Startup hits a fatal error, and on macOS that opens a **modal NSAlert** even under `-v null`. The log shows `Could not change to foreground application. Error -50` and the process blocks. Linux prints `Error: …` and exits. | `src/os/macosx/macos.mm:136-144` vs `src/os/unix/unix.cpp:193-199`; observed and killed |

**Rule:** generate one self-contained `openttd.cfg` per env with no `[version]` section, run with `-x`, override `HOME` on macOS, and put a timeout on every launch.

### 1.2 Verified config and launch

```ini
; Single-file config. No [version] section => secret/private settings are read from THIS file.
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
[construction]
command_pause_level = 3   ; allow construction while paused (see §5.3); cannot be changed at runtime in network games
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

`command_pause_level = 3` was verified in the Docker runs in §5.3. The macOS runs in this section used the file without it.

```bash
RUN=$SCRATCH/ottd/run; ISO_HOME=$SCRATCH/ottd/home
mkdir -p $RUN/baseset $ISO_HOME
cp <OpenGFX-8.0 .tar> $RUN/baseset/
cd $RUN && : > cmds.txt
# optional stdin console fed from a growing file; admin RCON is the real control channel
tail -n +1 -f cmds.txt | HOME=$ISO_HOME /Applications/OpenTTD.app/Contents/MacOS/openttd \
    -D -x -c $RUN/openttd.cfg -G 12345 > server.log 2>&1 &
nc -z 127.0.0.1 39770     # succeeds ~0.27 s after launch
```

### 1.3 Measurements (macOS 15.2, 64x64 unless noted, RCON from Python on loopback)

| Operation | Result |
|---|---|
| Pace at 1x | 1.998 s/day (admin `DATE` daily push; also stdin `getdate`) |
| Admin join (connect to `WELCOME`) | 28–46 ms |
| RCON `getdate`, `companies`, `pause`, `getseed` | 25–30 ms each |
| `save` | 27–34 ms (64²), 86 ms (512²); 14 KB to 339 KB |
| `ls` + `load` (to `WELCOME`) | 72–89 ms, 64² to 512² |
| `newgame <seed>` (to `WELCOME`) | 65–128 ms, 64² to 512² |
| Same-seed reset | Two `newgame 777` saves are byte-identical except the random 32-hex `savegame_id` in the `DATE` chunk |

### 1.4 Console and RCON gotchas

1. **The stdin console can stall.** `-D` reads stdin with `std::getline` after `select()`. Several queued lines can wait until the next write. Use admin RCON.
2. **`save` does not refresh the console save list.** `load <new>` then answers `'<name>' cannot be found`. Send `ls` before `load`.
3. **`setting pause_on_newgame true`** makes every reset start paused. The admin session survives `newgame` and `load`, receiving `NEWGAME` then `WELCOME`.
4. **`screenshot` is queued** to the main thread (`src/screenshot.cpp` `MakeScreenshot` → `QueueOnMainThread`), so its RCON reply returns before the file exists. Time it with a follow-up RCON.

---

## 2. Fast simulation

### 2.1 Stock 15.x: the dedicated server cannot fast-forward

The dedicated loop sleeps `27 ms × 100 / _game_speed`, where 0 means unlimited (`src/video/video_driver.hpp:309-313`). Only the GUI toolbar or hotkey calls `ChangeGameSpeed()` (`src/gfx.cpp:1858-1865`). `InitializeGame()` resets `_game_speed = 100` on every new game or load (`src/misc.cpp:101`). Paused games always tick at 27 ms. nttd independently reports "no `game_speed` setting in OpenTTD 15.3" (§6).

### 2.2 The Railhead patch (built and measured on 15.3)

The patch is inlined in `assets/Dockerfile.openttd-probe` and applies cleanly to 15.2 and 15.3.

1. `src/console_cmds.cpp` gains `gamespeed [<pct>]` (0 = unlimited), registered with `ConHookServerOrNoNetwork`, so it is usable from admin RCON.
2. `src/video/video_driver.hpp`: while paused **and** `_game_speed == 0`, the loop interval becomes 1 ms instead of 27 ms.

```cpp
if (_pause_mode.Any()) return std::chrono::milliseconds(_game_speed == 0 ? 1 : MILLISECONDS_PER_TICK);
```

| 15.3 patched image, admin `DATE` push / poll | Days/s | Note |
|---|---|---|
| `gamespeed 100` (64², empty) | 0.5 | stock pace |
| `gamespeed 1000` | 5.0 | exactly 10x |
| `gamespeed 5000` | 24.9 | |
| `gamespeed 0` (64², empty) | **5,322** | 44 game-years in 3 s; RCON median 0.26 ms |
| `gamespeed 0` (256², fresh map; 15.2 build) | 2,418 | |
| `gamespeed 0` (512², fresh map; 15.2 build) | 799 | |
| `gamespeed 0` (64²) **with a GameScript sending admin JSON every 100 Sleep(1) iterations** | ~1,220 | the GS loop ran 90,467 iterations/s; per-tick GS work dominates at unlimited speed |

Paused behaviour, measured with a probe GameScript (`RHTick`, a `Sleep(1)` loop reporting via `GSAdmin.Send` every 100 iterations):

| State | GS loop iterations/s | Game days advancing | RCON round trip |
|---|---|---|---|
| running, gamespeed 100 | 37 | yes | 27 ms |
| **paused**, gamespeed 100 (stock behaviour) | **37** | no | 27 ms |
| running, gamespeed 0 | 90,467 | yes (~1,220/s) | — |
| **paused, gamespeed 0 (patch part 2)** | **1,090** | no | **1.7 ms** |

Findings:

- **The GameScript does run while paused** (`src/openttd.cpp` `StateGameLoop` calls `Game::GameLoop()` in its paused branch), and `Sleep(1)` keeps returning. This contradicts nttd's statement that `Sleep` "counts ticks, of which a paused game delivers none" (§6). Their hang may come from something else. GS `DoCommand`s while paused were **not tested** here.
- `gamespeed` reads `100%` again after every `newgame` or `load`, so re-send `gamespeed 0` after each reset.
- The RAM footprint is unchanged at 7–9 MiB per dedicated server.
- Step precision: at about 5k days/s a 0.3 ms RCON round trip overshoots by 1–2 days. Use a GS-side target date or a moderate speed when exact stepping matters (**NOT RUN**).

### 2.3 Null video driver (offline batch only)

`-v null:ticks=N` runs N `GameLoop()`s with no sleep, writes `save/autosave/exit.sav` if `gui.autosave_on_exit = true`, then exits. It has no network or admin port, and it cannot advance a save stored **paused**: two such saves showed zero progress.

```bash
HOME=$ISO_HOME openttd -v null:ticks=200000 -s null -m null -x -c $DIR/openttd.cfg -G 12345 -g          # new game
HOME=$ISO_HOME openttd -v null:ticks=100000 -s null -m null -x -c $DIR/openttd.cfg -g $DIR/save/x.sav   # continue an unpaused save
```

| Fresh map | macOS app 15.2 | Linux container (15.2 dedicated build) |
|---|---|---|
| 64², 200k ticks (1950 → 1957-05-26) | 0.25 s → **811k ticks/s** | 0.23 s → 873k ticks/s |
| 256², 100k ticks | 0.41 s → 245k ticks/s | 0.44 s → 229k ticks/s |
| 512², 50k ticks | 0.81 s → 62k ticks/s | 0.93 s → 54k ticks/s |

Startup overhead is about 0.09 s per process. These maps have towns and industries but no player vehicles. **Vehicle-heavy throughput is not measured.** A world populated by an AI or GameScript is needed for that number.

---

## 3. Admin port: auth and the probe client

- **Protocol version 3.** `SERVER_PROTOCOL` frequency masks on 15.x are `{DATE:0x3f, CLIENT_INFO:0x41, COMPANY_INFO:0x41, COMPANY_ECONOMY:0x3d, COMPANY_STATS:0x3d, CHAT:0x40, CONSOLE:0x40, CMD_NAMES:0x01, CMD_LOGGING:0x40, GAMESCRIPT:0x40}`. `AdminUpdateFrequency` is a bitset: Poll 0x01, Daily 0x02, …, Automatic 0x40.
- **Plain `ADMIN_JOIN` (0).** It needs `network.allow_insecure_admin_login = true` (`src/network/network_admin.cpp:629`). Otherwise the server logs `made an error and has been disconnected: 'not authorized'`, and the client only sees the socket close. The setting can be flipped at runtime with `setting network.allow_insecure_admin_login …`. Both directions were verified.
- **Secure join.** The client sends `ADMIN_JOIN_SECURE` (9). The server answers `SERVER_AUTH_REQUEST` (128), the client replies `ADMIN_AUTH_RESPONSE` (10), and the server sends `SERVER_ENABLE_ENCRYPTION` (129). Every later packet is AEAD-encrypted (X25519 PAKE or authorized keys, Monocypher incremental AEAD).
- **Recommendation:** use plain join on a container-internal or loopback admin port.

**Probe client:** `$SCRATCH/client/probe_admin.py`, stdlib only, about 200 lines. It was written from the packet enums in `src/network/core/tcp_admin.h` and the senders and receivers in `src/network/network_admin.cpp`. Scenarios are `basic | speed | saveload | newgame | scale | determinism | disable_insecure | insecure_off`. Helpers are `rcon_cli.py`, `probe_ffwd.py` and `probe_gs_tick.py`.

```
joined in 37.5 ms: {'admin_protocol': 3, ..., 'revision': '15.2', 'dedicated': True, 'seed': 12345, 'map_x': 64, 'map_y': 64}
rcon 'getdate' (26.9 ms): ['Date: 1950-02-05']
rcon save probe_a: 29.8 ms -> ['Saving map...', "Map successfully saved to 'probe_a.sav'."]
```

---

## 4. Docker: patched 15.3 source build (the Railhead route)

### 4.1 The Dockerfile

`docs/research/assets/Dockerfile.openttd-probe` is self-contained, with the patch inlined via a BuildKit heredoc, so it needs no build context.

- **Source:** `openttd-15.3-source.tar.xz` from cdn.openttd.org, with sha256 `5ea21eea…3be7` from the release `manifest.yaml`. It is identical to git tag 15.3 (`14ec60f…`).
- **Build stage:** `debian:trixie-slim` (gcc 14, cmake 3.31). Configure, build and install:

```
cmake -S openttd -B build -G Ninja -DCMAKE_BUILD_TYPE=Release -DOPTION_DEDICATED=ON -DOPTION_INSTALL_FHS=OFF -DCMAKE_INSTALL_PREFIX=/opt/openttd
cmake --build build -j$(nproc)
cmake --install build --strip
```

- **`OPTION_INSTALL_FHS=OFF`** puts the binary next to `lang/`, `baseset/`, `ai/`, `game/` and `scripts/`, 34 MB in total.
- **OpenGFX:** `https://cdn.openttd.org/opengfx-releases/8.0/opengfx-8.0-all.zip` provides `opengfx-8.0.tar` in `baseset/`.
- **Runtime stage:** `debian:trixie-slim` as non-root `ottd` (uid 1000). `HEALTHCHECK` runs `nc -z 127.0.0.1 3977`, and `ENTRYPOINT` is the binary with `CMD -D -x`.
- **`ARG RAILHEAD_PATCH=1`.** Set 0 for a vanilla build.

### 4.2 Exact apt dependencies for a dedicated-only build

| Purpose | Packages (Debian trixie) |
|---|---|
| **Required to compile** | `build-essential cmake ninja-build zlib1g-dev liblzma-dev liblzo2-dev` |
| Optional, included | `libpng-dev`, for PNG heightmap loading and PNG screenshots (otherwise BMP only) |
| Optional, omitted | `libcurl4-openssl-dev`, needed only for in-game content download from BaNaNaS; bake GS libraries into the image instead |
| Not needed | `pkg-config`, SDL2, freetype, fontconfig, harfbuzz, ICU, fluidsynth, OpenGL, which are skipped under `OPTION_DEDICATED` |
| Fetch and patch only | `ca-certificates curl xz-utils unzip patch` |
| **Runtime** | `zlib1g liblzma5 liblzo2-2 libpng16-16t64` (+ `netcat-openbsd` for the healthcheck) |

CMake output for this set: `Found ZLIB 1.3.1, LibLZMA 5.8.1, LZO, PNG 1.6.48`. Not found, and harmless: CURL, Doxygen, Grfcodec, breakpad. A dedicated-only build compiles only the `null` blitter (`src/blitter/CMakeLists.txt`), and its video drivers are `dedicated` and `null`.

### 4.3 Measured (linux/arm64, colima 4 vCPU)

| Metric | Value |
|---|---|
| Cold build, `--no-cache` | **182 s** wall: apt 14 s, compile and install **163 s**, OpenGFX 1 s |
| Cached rebuild from the asset file | 2 s |
| Image | **191 MB** on disk, **45.1 MB** compressed (`docker image inspect` reports 45,067,361 bytes) |
| `docker run` to admin port listening | 0.28–0.33 s |
| RSS per server | 7.4–8.5 MiB (64²–256²) |
| Verified from the asset | healthy, runs as `ottd`, `gamespeed 0` works, paused RCON in 1 ms |

```bash
docker build -t railhead/openttd:15.3 - < docs/research/assets/Dockerfile.openttd-probe
docker run -d --name ottd -p 127.0.0.1:39771:3977 -e CFG="$(cat openttd.cfg)" --entrypoint sh railhead/openttd:15.3 \
  -c 'mkdir -p ~/r && printf "%s\n" "$CFG" > ~/r/openttd.cfg && exec /opt/openttd/openttd -D -x -c ~/r/openttd.cfg -G 12345'
```

- **Config injection:** the config is passed through an env var because colima shares only `$HOME` with its VM. A bind mount from `/private/tmp` arrives empty, as `04-harbor.md` also found.
- **Multi-arch:** build natively per arch, for example GitHub `ubuntu-24.04-arm` plus an amd64 runner, then `docker buildx imagetools create`. QEMU emulation of this C++ build was **NOT RUN**.
- **Other routes, one line:** cdn.openttd.org ships only `linux-generic-amd64` tarballs (no arm64, and a GUI build that needs `LD_LIBRARY_PATH=./lib`). Distros lag at 15.1 (Debian sid/forky, Ubuntu 26.04), and the apt image is 728 MB.

---

## 5. Visual observation channel

### 5.1 Server-side screenshots via RCON (no client needed)

| Server binary | `screenshot minimap` (1 px/tile) | `screenshot heightmap` | `screenshot giant` (whole map, isometric, zoom default) | RSS |
|---|---|---|---|---|
| Dedicated-only build (Railhead image) | **works**: 256² → 256x256 PNG, 3.9 KB, 15–55 ms | works | **fails** ("Screenshot failed!", null blitter, no crash) | 7–9 MiB |
| GUI build run as `-D -b 32bpp-optimized` (`-b` after `-D`) | works | works (NOT RUN) | **works**: 64² → 3969x2135, 1.5 MB in 0.34–0.43 s; 256² → 16257x8271, 28 MB in 4.8 s (macOS) | 58 MiB |

This was verified on the macOS app 15.2 and on the Linux GUI image 15.3 (64²: 335 ms, 1.5 MB). The dedicated driver allocates a real framebuffer whenever the blitter has a non-zero depth (`src/video/dedicated_v.cpp` `Start`).

- **`normal` and `big` screenshots:** these call `GetMainWindow()`, which asserts on a dedicated server (`src/screenshot.cpp:198,242`, `src/window.cpp:1182-1187`), so they are unusable server-side (**NOT RUN**).
- **Where files land:** in `<personal dir>/screenshot/`. Read them via a volume or `docker exec cat`.
- **Model-sized views:** crop or downscale the giant image client-side, or use §5.2.

### 5.2 GUI client under Xvfb in Docker (the human view, plus computer-use input)

The probe image `railhead/openttd-gui:15.3` is the same Dockerfile with `-DOPTION_DEDICATED=OFF`. It adds these build deps: `libsdl2-dev libfreetype-dev libfontconfig-dev libharfbuzz-dev libicu-dev`. It adds these runtime packages: `libsdl2-2.0-0 libfreetype6 libfontconfig1 libharfbuzz0b libicu76 xvfb xauth scrot xdotool`. Build time was 234 s, 164 s of it compiling. The image is 709 MB on disk, 181 MB compressed. Client launch:

```bash
rm -f /tmp/.X99-lock    # a restarted container otherwise fails: "Unable to load driver 'sdl'. ... x11 not available"
Xvfb :99 -screen 0 1280x720x24 -nolisten tcp &
DISPLAY=:99 SDL_VIDEODRIVER=x11 openttd -v sdl -b 32bpp-optimized -s null -m null -r 1280x720 -x -c ~/c/openttd.cfg -n rhsrv:3979
#  -n host:port         -> joins as a NEW company (COMPANY_NEW_COMPANY, src/openttd.cpp:444)
#  -n host:port#1       -> joins company 1;  #255 -> spectator  (src/network/network.cpp:476-500)
#  client cfg needs [network] client_name = ...  (empty name aborts the join)
```

Measured, with the server and client in separate containers on one Docker network:

| Step | Result |
|---|---|
| Client joins the dedicated server | **1.2 s** after `docker run`. The server lists `railhead-viewer` in company 1 and founds "railhead-viewer Transport" with £100,000. |
| Frame capture | `docker exec … scrot` gives a 1280x720 PNG, about 850 KB, in **147 ms** including `docker exec` overhead. It shows the toolbar, viewport, money, date and news. |
| Input | `xdotool` clicks drove the toolbar, then Road construction, Auto-road and a drag on the map. The road was **built at a cost of £673**, and the admin `companies` money went 100,000 → 99,327, confirming it executed on the server. |
| Client RSS / CPU | 112–124 MiB, about 25–50% of one core |
| Server fast-forward with a GUI client attached | 1000% gave 5.0 days/s and 5000% 25.0; 10000% gave 50.2, and 20000% gave **96 days/s, still in sync**. At **40000% the client was dropped**: "Client #3 … is dropped because the client's game state is more than 501 ticks behind". At unlimited speed it was dropped immediately. |

### 5.3 Building while paused, which matters for any lockstep "act while paused" design

| Server setting | GUI road build while paused |
|---|---|
| `construction.command_pause_level = 1` (default) | **refused**: red box "Can't build road here... Not allowed while paused"; money unchanged |
| `= 3`, set in `openttd.cfg` at startup | **executed**: money 100,000 → 99,095, date frozen at 1950-01-02 |
| `setting construction.command_pause_level 3` over RCON | **rejected**: "This command/variable is not available during network games." |

nttd sets the same value (§6).

---

## 6. Prior art: who lets an AI control a company directly

Searches were run on 2026-10-16. Metadata comes from the PyPI JSON API and the GitHub REST API, and code was read, not executed.

| Project | Control channel | OpenTTD version | Status | Notes |
|---|---|---|---|---|
| **nttd** (`deepsaia/nttd`, PyPI `nttd` 0.0.7) | Dedicated server, **admin port** (`pyopenttdadmin`), a **GameScript bridge** (`ottd_config/game/nttd-gs/main.nut`, 5,682 lines, `GSCompanyMode(cid)`), plus an idle AI | 15.3 (macOS app) | Apache-2.0, 3 stars, created 2026-03-10, pushed 2026-09-05; "developed and tested on macOS only" | Agent-agnostic "long-horizon planning benchmark". REST, **MCP** (5 tools) and **Gym** (`NttdEnv`) surfaces. 76 participant actions bounded by **human parity, "the GUI"**. Real-time and **stepped** modes at 1x (about 1.97 s/day), with no speed control. Sets `command_pause_level = 3`. Scores to parquet. Reference agents in `deepsaia/nttd-workbench`. |
| **pyttd** (`mssc89/pyttd`, PyPI 1.3.0) | **Network game client**: joins as a player, sends DoCommands, parses the map/savegame | "Tested with 14.1" | MIT, 0 stars, 2025-08 | Its packet enum is the pre-14 auth flow (`SERVER_NEED_GAME_PASSWORD`…). 15.3 uses `SERVER_AUTH_REQUEST`/`ENABLE_ENCRYPTION` (`src/network/core/tcp_game.h`), so it **will not join 15.x** as is. |
| **OpenTTDLab** (`michalc/OpenTTDLab`) | Runs **NoAI** scripts headless, then parses savegames | ≤ 13.4 ("does not support 14.*") | GPL-2.0, 49 stars, pushed 2025-06 | Reproducible AI experiments and BaNaNaS download, not interactive control |
| **RSI-bench "OpenTTD Transport AI"** task | The agent **improves a NoAI script**, which is replayed 20 game years on sealed seeds and scored by company value | unknown | site unreachable from here (DNS failure); details from the search snippet only, **unverified** | Indirect control: the model writes the policy, not the moves |
| TycoonLE | JAX environment "inspired by" OpenTTD; not OpenTTD | — | one blog source, no repository found, **unverified** | |
| Academic NoAI work | DTU 2011 thesis "SPRING"; Utrecht thesis on grammatical evolution and dynamic scripting AIs | old | — | |
| GUI or computer-use agents playing OpenTTD | — | — | **none found** | §5.2 shows it is technically feasible |

Admin-port Python libraries:

| Library | 15.x secure auth | Status |
|---|---|---|
| **pyOpenTTDAdmin** (PyPI `pyopenttdadmin` 1.1.0, 2026-06-12) | Yes (bundled Monocypher C extension; wheels cp39–cp314, Linux aarch64/x86_64, macOS) | active; nttd depends on it |
| libottdadmin2 (`xaroth`, 0.0.4a1, git only) | Yes (2024 PR from `rubidium42`), needs `pymonocypher` | last push 2024-07; **CC BY-NC-SA 3.0** licence header |
| python3-openttd (`horazont`, 0.2.0, 2021) | No | stale |
| `openttd-protocol` (official `OpenTTD/py-protocol`) | n/a, no admin protocol | backend services only |

---

## 7. Direct-control channels compared (with this session's measurements)

| Channel | Same action set as a human? | Feedback | Survives fast-forward? | Version coupling | Cost |
|---|---|---|---|---|---|
| **GameScript bridge in company mode** (admin `ADMIN_GAMESCRIPT` ⇄ GS) | Close. `GSCompanyMode` covers rail, road, stations, vehicles, orders, loan and terraform (see `03-openttd-source.md` §2.1). GUI-only conveniences must be re-implemented. | Synchronous return plus `GSError` | **Yes.** It runs inside the server tick, and the GS loop reached 90k iterations/s at unlimited speed. | GS API versioned, with compat shims | about 8 MiB per env |
| **NoAI script** (`start_ai`) | Same API family as GS, company-bound | In-script only | Yes | Same | The model writes code rather than playing |
| **Network game client bot** (pyttd-style) | Yes: raw DoCommands, exactly what the GUI sends | None unless it simulates. Command errors surface only in the issuing client's own simulation. | **No.** It is dropped above `max_lag_time` (500, max 32,000 ticks) unless it simulates as fast as the server. | **Exact revision.** Internal command IDs and argument layouts change per release, and 15.x adds encryption (pyttd is already broken). | Must also handle the full map download per reset |
| **GUI client plus computer use** (Xvfb, scrot, xdotool) | **Exactly the human action set** | Visual: error boxes, cost labels, money | Up to about 200x (96 days/s on 128²), dropped at 400x+ | Same revision as the server (same image recipe) | about 120 MiB, a 709 MB image, and per-frame screenshot plus model latency |

**Recommendation:** use the GameScript company-mode bridge as the primary action and observation channel, since it is fast-forward safe. Offer the Xvfb GUI client as an optional visual channel, and as an optional pure computer-use control mode, run lockstep with the world paused (`command_pause_level = 3`) or at ≤200x. Server-side `minimap` (cheap) and `giant` (GUI build only) screenshots give a visual observation without any client.

---

## 8. Ops recipe

1. **Image:** use `assets/Dockerfile.openttd-probe`, which is 15.3 plus the patch, dedicated-only, 191 MB. Pin the same 15.3 on any macOS dev box, because a GUI client must match the server revision exactly.
2. **Per env:** one server with its own `-c` dir and ports, plus `-x`. Config: no `[version]` section, `allow_insecure_admin_login = true`, `[construction] command_pause_level = 3`, `autosave_interval = 0`.
3. **Bootstrap once:** `setting pause_on_newgame true`.
4. **Reset:** `newgame <seed>`, or `ls` then `load <snapshot>`. Wait for `WELCOME` (65–130 ms), then send `gamespeed 0` (or the chosen speed) again.
5. **Step:** `unpause`, then wait for the target `DATE` or a GS signal, then `pause`. While paused at gamespeed 0, RCON and GS round trips take about 1–2 ms.
6. **Snapshot:** `save <name>` takes 27–86 ms. Send `ls` before any `load`.
7. **Visual (optional):** send `screenshot minimap <name>` over RCON, or attach an Xvfb GUI client and keep the speed at ≤200x while it is attached.
8. **Safety:** put timeouts on every launch (macOS `-X` hang), and clear `/tmp/.X99-lock` before restarting an Xvfb client container.

### Files produced (scratchpad, not committed)

`$SCRATCH` = `/private/tmp/claude-501/-Users-rachitt/a8c1f8a7-1e71-4e78-bc25-15b2761b9b4f/scratchpad`

- **Clients:**
  - `$SCRATCH/client/probe_admin.py` (stdlib admin client, written from the 15.x source)
  - `rcon_cli.py`, `probe_ffwd.py`, `probe_ffwd_save.py`, `probe_gs_tick.py`
- **GameScript:** `$SCRATCH/gs/RHTick/{info,main}.nut` (tick probe)
- **Docker:**
  - `$SCRATCH/docker/v2/Dockerfile` (= the asset)
  - `$SCRATCH/docker/gui/Dockerfile` (GUI/Xvfb probe)
  - `$SCRATCH/docker/railhead-gamespeed-15.3.patch`
- **Configs:**
  - `$SCRATCH/docker/*.cfg` (server and client configs)
  - `$SCRATCH/ottd/openttd.full-15.2.cfg` (full 460-line settings dump)
- **Frames:** `$SCRATCH/shots/*.png`, the Xvfb client frames and server-side screenshots referenced above
- **Images kept locally:** `railhead/openttd:15.3` and `railhead/openttd-gui:15.3`
