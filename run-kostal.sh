#!/bin/bash
set -euo pipefail

# Directory of this script, so the project works in any folder.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$PROJECT_DIR/kostal-battery.env"
LOG_FILE="$PROJECT_DIR/kostal-battery.log"

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

# Seasonal reserve: summer values from 31 March to 31 August, rising linearly
# from 1 September to the winter values on 21 December, then falling linearly
# back to the summer values by 31 March.
SUMMER_BUFFER_MIN=30
SUMMER_SAFETY_PCT=10
WINTER_BUFFER_MIN=240
WINTER_SAFETY_PCT=75

# Days since the epoch for a YYYY-MM-DD date.
day_number() {
    echo $(( $(date -u -d "$1" +%s) / 86400 ))
}

# SEASON_DATE (YYYY-MM-DD) overrides today's date for testing.
SEASON_DATE="${SEASON_DATE:-$(date +%F)}"
YEAR=$(date -d "$SEASON_DATE" +%Y)
TODAY=$(day_number "$SEASON_DATE")
MAR31=$(day_number "$YEAR-03-31")
SEP1=$(day_number "$YEAR-09-01")
DEC21=$(day_number "$YEAR-12-21")

# Winter share in per mille: 0 = summer values, 1000 = winter values.
if (( TODAY >= MAR31 && TODAY < SEP1 )); then
    WINTER_SHARE=0
elif (( TODAY >= SEP1 && TODAY <= DEC21 )); then
    WINTER_SHARE=$(( (TODAY - SEP1) * 1000 / (DEC21 - SEP1) ))
elif (( TODAY > DEC21 )); then
    NEXT_MAR31=$(day_number "$((YEAR + 1))-03-31")
    WINTER_SHARE=$(( 1000 - (TODAY - DEC21) * 1000 / (NEXT_MAR31 - DEC21) ))
else
    PREV_DEC21=$(day_number "$((YEAR - 1))-12-21")
    WINTER_SHARE=$(( 1000 - (TODAY - PREV_DEC21) * 1000 / (MAR31 - PREV_DEC21) ))
fi

SUNSET_BUFFER_MIN=$(( SUMMER_BUFFER_MIN + ((WINTER_BUFFER_MIN - SUMMER_BUFFER_MIN) * WINTER_SHARE + 500) / 1000 ))
FORECAST_SAFETY_PCT=$(( SUMMER_SAFETY_PCT + ((WINTER_SAFETY_PCT - SUMMER_SAFETY_PCT) * WINTER_SHARE + 500) / 1000 ))

APPLY_ARGS=()

if [[ "${1:-}" == "--apply" ]]; then
    APPLY_ARGS+=(--apply)
fi

"$PROJECT_DIR/.venv/bin/python" \
    "$PROJECT_DIR/kostal_battery_optimization.py" \
    --host kostal-wr1.ciscofreak.local \
    --solar-auto \
    --clear-other-days \
    --sunset-buffer-minutes "$SUNSET_BUFFER_MIN" \
    --forecast-safety-percent "$FORECAST_SAFETY_PCT" \
    --base-load-w 800 \
    --battery-capacity-kwh 22.1 \
    --south-resource-id c0f0-2f3d-2107-648b \
    --north-resource-id 30f4-9257-92b0-6819 \
    "${APPLY_ARGS[@]}" 2>&1 | tee -a "$LOG_FILE"
