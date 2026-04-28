#!/usr/bin/env bash
# Daily FDI agent pipeline — install with `crontab -e` and paste the line below.
#
# Runs at 02:00 every night in the host's local time. Output goes to
# /var/log/fdi-agent/daily.log (rotated by logrotate; sample config below).
#
# Run as a non-root user that has read/write on the project + /var/log path.

set -euo pipefail

# Edit this to match your install path
PROJECT_DIR="${PROJECT_DIR:-/opt/fdi-agent}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/.venv}"
LOG_DIR="${LOG_DIR:-/var/log/fdi-agent}"

mkdir -p "$LOG_DIR"
cd "$PROJECT_DIR"

# Load secrets from .env if present
if [ -f "$PROJECT_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  . "$PROJECT_DIR/.env"
  set +a
fi

# shellcheck disable=SC1091
. "$VENV_DIR/bin/activate"

LOG_FILE="$LOG_DIR/daily-$(date +%Y%m%d).log"

echo "=== fdi-agent daily run started $(date -Iseconds) ===" >> "$LOG_FILE"
python -m orchestrator.daily_run >> "$LOG_FILE" 2>&1
EXIT=$?
echo "=== fdi-agent daily run finished $(date -Iseconds) exit=$EXIT ===" >> "$LOG_FILE"

# Optional: fail-loud notification on non-zero exit
if [ $EXIT -ne 0 ] && [ -n "${SLACK_WEBHOOK_URL:-}" ]; then
  curl -s -X POST -H 'Content-type: application/json' \
    --data "{\"text\":\"⚠️ fdi-agent daily run exited with code $EXIT — see $LOG_FILE\"}" \
    "$SLACK_WEBHOOK_URL" || true
fi

exit $EXIT
