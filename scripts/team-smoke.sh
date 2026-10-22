#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
set -a; . deploy/compose/.env; set +a
docker compose -f deploy/compose/docker-compose.yml -f deploy/compose/stepnoe.yml \
  exec -T -e DITTO_PASSWORD -e GRAFANA_ADMIN_PASSWORD -e INFLUXDB_TOKEN stepnoe python smoke.py
