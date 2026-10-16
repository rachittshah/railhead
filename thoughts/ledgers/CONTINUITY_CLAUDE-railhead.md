# CONTINUITY — railhead

## Goal
Build "Railhead": a frontier RL environment on OpenTTD where **models play the game directly**
(same action set as a human player; no advisor layer, no scripted autopilot).
Done = (1) abstractions built + tested, (2) RL env built + tested, (3) code on GitHub
(rachittshah/railhead), (4) env published to Harbor registry (`harbor publish`),
(5) dataset on Hugging Face, (6) any GUI shown on the LG ULTRAGEAR monitor only.

## Constraints
- User directive 2026-10-16: IGNORE ~/ottd-claude (don't reuse/evaluate).
- Models play directly: player-level primitive commands are the action space. Convenience
  only via code the model writes itself (SDK), never env-side autopilots.
- Build + test abstractions FIRST, then the RL env.
- Use `uv pip install`; no one-off test scripts — tests live in the repo's test suite.
- Git commits via the `/commit` skill (user rule). No Claude attribution per that skill.
- LG ULTRAGEAR: Cocoa frame x=-1080 y=-480 w=1080 h=1920 (portrait), i.e. top-left coords
  x∈[-1080,0), y∈[0,1920). Any GUI window (OpenTTD spectator, browser) goes there.
- Harbor 0.22.0 CLI at ~/.local/bin/harbor (not logged in). HF CLI not logged in. gh logged in.
- Docker via colima (default profile, aarch64).
- OpenTTD 15.2 at /Applications/OpenTTD.app; OpenGFX 8.0 tar in ~/Documents/OpenTTD/content_download/baseset.

## Key Decisions
- Name: Railhead (the advancing end of track under construction = the frontier).
- Python ≥3.11, stdlib-first core; optional extras gym/hub.
- Bridge (pending SME verification): Python ⇄ Admin Port ⇄ custom GameScript using GSCompanyMode.
- Harbor (docs/research/04-harbor.md, verified): single `main` container w/ OpenTTD; idempotent
  `railhead-up` from ENTRYPOINT + [environment.healthcheck] + lazily from `rh` CLI. Verifier is
  shared-mode (same live container) → reward.json {reward∈[0,1], diagnostics...}. Shell CLI is
  mandatory (terminus-2 has no MCP). Ship Dockerfile (FROM ghcr image) AND docker_image (public GHCR).
  STOPSIGNAL SIGKILL. ≤1 CPU/1 GB target (SkyRL clamps). Harbor jobs dir must be under $HOME (colima).
  Task→scenario selection via env var + healthcheck arg + Dockerfile ENV; start-states baked in image.
  harbor auth = GitHub OAuth needing one human click (try via Chrome on LG monitor); or HARBOR_API_KEY.

## State
- Done:
  - [x] Phase 0: scaffold, SME research (docs/research/01-04), DESIGN.md (M1-M5)
  - [x] Phase 1: engine patches, admin/bridge/World, runtime (log, replay, daemon, forks, waits,
        ledger), 42 player actions (GUI parity), queries + observe, rh/railhead CLIs
  - [x] Phase 2: rewards, seal, verifier (+turn credit), generators (deliver, deliver_bid,
        impossible, tycoon, simops), road oracle, Harbor export (per-arch manifests), HF builder
  - [x] Verified: 184+ tests; Linux CI green; macOS-generated tasks replay identically on Linux;
        local harbor run: oracle 1.0 / nop 0.0 on deliver+simops, zero divergences
  - [x] Pushed to https://github.com/rachittshah/railhead (public); STE docs (README, docs/*)
  - [x] Harbor CLI logged in (rachittshah) via no-browser OAuth (Brave window on LG monitor)
  - [x] railhead view: GUI spectator on LG monitor lower half (RailheadViewer.app in .cache)
- Now: [→] full catalog generation (scratchpad gen_catalog.sh), player-deliver subagent playing
- Next: commit tasks/ → tag v0.1.0 → CI multi-arch image → GHCR public → export (amd64+arm64
        manifests) → harbor publish (railhead = dev+ood, railhead-train = train) → HF push
- Remaining:
  - [ ] HF auth: user must run `hf auth login` (I won't mint tokens in their account)
  - [ ] GHCR package visibility public (may need user click in GitHub settings)
  - [ ] Final LG monitor showcase + README results table

## Open Questions
- RESOLVED: fast-forward needs patch 0001 (done, ~3-5k days/s).
- RESOLVED: GS runs while paused; GS covers 79/100 player cmds (gaps: timetables, service intervals, semaphores...).
- RESOLVED: 1456 B stock; 32 KB with patch 0003.
- RESOLVED: yes (ENTRYPOINT/healthcheck). Use SHARED verifier mode (separate mode in 0.22 skips healthcheck + needs custom tests image). valid key must be int.
- RESOLVED: Harbor auth done. OPEN: HF token (user action). Brave is the default browser (no Chrome).

## Working Set
- Repo: ~/railhead (branch main)
- Research: docs/research/01..04*.md
- Tests: `.venv/bin/pytest -q` (unit), `-m integration` (needs OpenTTD)
