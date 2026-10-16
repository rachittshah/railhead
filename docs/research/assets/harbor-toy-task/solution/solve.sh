#!/bin/bash
# Oracle: Harbor uploads solution/ to /solution and runs this via `docker compose exec main bash -c ...`.
set -euo pipefail

printf 'railhead' > /srv/www/hello.txt

for _ in 1 2 3; do
  python -c "import urllib.request as u; u.urlopen(u.Request('http://game:9000/tick', method='POST'), timeout=5)"
done

# Evidence for the verifier: what the agent phase saw.
cat /run/svc/pid > /tmp/svc_pid_seen_by_agent
python -c "import json,urllib.request as u; print(json.load(u.urlopen('http://game:9000/state'))['boot_id'])" > /tmp/game_boot_id_seen_by_agent

# Probe: does a daemon the AGENT starts (via exec) survive into the verifier phase?
nohup python -m http.server 8081 --directory /srv/www >/tmp/agent_svc.log 2>&1 &
echo $! > /tmp/agent_svc.pid

echo "solve.sh done: svc_pid=$(cat /run/svc/pid) game_boot_id=$(cat /tmp/game_boot_id_seen_by_agent)"
