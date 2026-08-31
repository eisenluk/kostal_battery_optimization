#!/bin/bash
set -euo pipefail

PROJECT_DIR="/home/eisenluk/kostal_battery_optimization"
ENV_FILE="/home/eisenluk/kostal_battery_optimization/kostal-battery.env"

# Prevent overlapping executions.
exec 9>"$PROJECT_DIR/kostal-battery.lock"
flock -n 9 || exit 0

if [[ ! -r "$ENV_FILE" ]]; then
    echo "Environment file is missing or unreadable: $ENV_FILE" >&2
    exit 1
fi

set -a
source "$ENV_FILE"
set +a

if [[ -z "${KOSTAL_PASSWORD:-}" ]]; then
    echo "KOSTAL_PASSWORD is missing" >&2
    exit 1
fi

if [[ -z "${SOLCAST_API_KEY:-}" ]]; then
    echo "SOLCAST_API_KEY is missing" >&2
    exit 1
fi

APPLY_ARGS=()

if [[ "${1:-}" == "--apply" ]]; then
    APPLY_ARGS+=(--apply)
fi

exec "$PROJECT_DIR/.venv/bin/python" \
    "$PROJECT_DIR/kostal_battery_optimization.py" \
    --host kostal-wr1.ciscofreak.local \
    --solar-auto \
    --sunset-buffer-minutes 30 \
    --forecast-safety-percent 10 \
    --base-load-w 750 \
    --battery-capacity-kwh 22.1 \
    --south-resource-id c0f0-2f3d-2107-648b \
    --north-resource-id 30f4-9257-92b0-6819 \
    "${APPLY_ARGS[@]}"
