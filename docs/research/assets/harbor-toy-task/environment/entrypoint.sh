#!/bin/sh
# Start the long-running service in the background, then hand PID 1 to
# whatever command Harbor passes (`sh -c "sleep infinity"`).
mkdir -p /run/svc
python -m http.server 8080 --directory /srv/www >/var/log/svc.log 2>&1 &
echo $! > /run/svc/pid
date +%s > /run/svc/started_at
exec "$@"
