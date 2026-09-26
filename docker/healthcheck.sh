#!/usr/bin/env bash
# Container healthcheck: API responds AND its own /health report says the
# configured DB backend is reachable.
set -euo pipefail

response="$(curl -fsS http://127.0.0.1:8000/health)" || exit 1
echo "$response" | grep -q '"db_ok": *true' && exit 0
echo "$response" | grep -q '"db_ok":true' && exit 0
exit 1
