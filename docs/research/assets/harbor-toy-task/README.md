# railhead/toy-service

Harbor 0.22.0 probe task for Railhead. It proves that long-running services survive
from container start, through the agent phase, into the verifier phase.

- `main` starts an HTTP server on :8080 from its ENTRYPOINT (Harbor overrides CMD, not ENTRYPOINT).
- The `game` sidecar (environment/docker-compose.yaml) keeps in-memory state the agent mutates.
- `tests/check.py` writes a multi-key `/logs/verifier/reward.json` with liveness metrics.

Run locally (docker backend):

```bash
harbor run -p <this-dir> -a oracle -e docker -o ~/some/dir/under/HOME/jobs
```

On colima, the jobs dir must be under $HOME. Colima only shares $HOME with its VM, so
bind-mounted /logs from elsewhere stay empty and the trial fails with RewardFileNotFoundError.

Expected reward.json: reward=1.0, game_ticks=3, cmd_service_alive=0, every other probe=1.
