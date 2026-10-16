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
  - [x] Phase 0a: workspace + scaffold (~/railhead, .venv, pyproject)
- Now: [→] Phase 0b: SME research team (5 agents) → docs/research/*.md
- Next: Phase 0c: design synthesis docs/DESIGN.md + adversarial critique
- Remaining:
  - [ ] Phase 1: abstractions (admin protocol codec+client, server manager, GS bridge, world DB,
        action API, action log/replay, clock, fork) + unit & integration tests
  - [ ] Phase 2: RL env (scenarios/task families, rewards/verifiers, CLI `rh`, env API, experts/oracles)
  - [ ] Phase 3: Harbor tasks + dataset (local `harbor run -a oracle` green)
  - [ ] Phase 4: Docker image (multi-arch, GHCR) ; GitHub push ; Harbor publish ; HF dataset
  - [ ] Phase 5: live demo on LG monitor (spectator client), final README

## Open Questions
- UNCONFIRMED: dedicated-server fast-forward possible without patching?
- UNCONFIRMED: GS runs while paused? GSCompanyMode can build everything in 15.2?
- UNCONFIRMED: max GS JSON payload size over admin port in 15.2.
- UNCONFIRMED: Harbor container start — can a background OpenTTD server persist through agent + verifier?
- UNCONFIRMED: Harbor/HF auth — need user's GitHub OAuth (harbor) and HF token.

## Working Set
- Repo: ~/railhead (branch main)
- Research: docs/research/01..04*.md
- Tests: `.venv/bin/pytest -q` (unit), `-m integration` (needs OpenTTD)
