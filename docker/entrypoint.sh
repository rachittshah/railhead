#!/bin/bash
# Boot the task's world in the background (idempotent), then run whatever Harbor asked for.
if [ -n "${RAILHEAD_TASK:-}" ]; then
  (/usr/local/bin/railhead-up >/var/lib/railhead/up.log 2>&1 &)
fi
exec "$@"
