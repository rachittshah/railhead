# Changelog

## 0.1.2 (unreleased)

Not published yet. The latest published image and Harbor datasets are 0.1.1.

### Fixed

- One version. `railhead.__version__` is the single source: `pyproject.toml` reads it, the default image
  tag and the Harbor task version derive from it, and the image workflow passes it as the build argument
  `RAILHEAD_VERSION`. The image build stops if the two differ, and a `v*` tag must name the same version.
  The `0.1.1` image reported `0.1.0`, and `railhead export` named the `0.1.0` image, which has no start states.
- The 25 ten-year arena tasks (`arena-train-0201` to `-0229`, `arena-dev-0212` to `-0233`) told the agent
  the linear tycoon scale ("a simple scripted truck route scores 0.5"). They are scored on the log scale;
  the briefing now says so. Rewards and certificates are unchanged (`railhead recheck`: 25 of 25 match).
- The quick start and the reference documents used task IDs that no longer exist. A unit test now checks
  every task ID, relative link and image path in `README.md` and `docs/*.md`.
- `railhead view` opened only when a display named "LG" was connected, and ignored `--bounds`. It now
  falls back to the main display, parses `--bounds x,y,w,h`, and gives each `osascript` call a timeout.

### Added

- `LICENSE` (MIT), `LICENSES/GPL-2.0-only.txt` and `NOTICE`, which says which paths are under which licence.
  The image carries the licence texts of OpenTTD, its third-party parts and OpenGFX, the upstream commit,
  the engine patches and build script, and a written offer of the source code (`/opt/railhead/licenses/`).
- `docs/TASKS.md`: the opaque task ID format and the `arena` family.
- `docs/USAGE.md`: how to make and publish the two Harbor datasets.
- README: results at a glance, and what the arena does not show.
- `engine/build.sh` takes `CXX_FLAGS` and `EXTRA_PATCHES` to build a variant engine into its own tree, and
  `engine/optional/0004-stdin-poll.patch` is one such patch (about 11% faster on an M4 Max, same checksums).
  The default build is unchanged, so sealed tasks pin the same engine binary.

### Changed

- A build on water or on the low side of a shore now fails with `ERR_CANNOT_BUILD_ON_WATER`. It was
  `ERR_TUNNEL_CANNOT_BUILD_ON_WATER`, the engine's name, which misled players (no tunnel was involved). Only
  `bridge.tunnel` keeps the old code. Code that matches on the old code must accept both. Replay is not
  affected: it compares success or failure, not the code.
- CI caches the installed engine directory, builds it once per change to `engine/`, and runs `ruff check`
  (ruff pinned to 0.17.4).
- The internal working ledger under `thoughts/` is no longer tracked.
