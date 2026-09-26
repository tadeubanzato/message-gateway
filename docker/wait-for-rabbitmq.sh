#!/usr/bin/env bash
# Waits for the in-container RabbitMQ to accept connections before exec'ing
# the real command. RabbitMQ runs as its own supervised process in this same
# container (not a separate service), so this is a same-container startup
# race, not a cross-container network wait.
set -euo pipefail

echo "[wait-for-rabbitmq] waiting for 127.0.0.1:5672..."
for i in $(seq 1 60); do
  if (exec 3<>/dev/tcp/127.0.0.1/5672) 2>/dev/null; then
    exec 3<&- 3>&-
    echo "[wait-for-rabbitmq] RabbitMQ is reachable."
    break
  fi
  sleep 1
done

exec "$@"
