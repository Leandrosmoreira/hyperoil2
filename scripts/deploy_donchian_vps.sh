#!/usr/bin/env bash
# =============================================================================
# Donchian VPS deploy — pull, install, warm-up, start in paper mode.
# =============================================================================
# Run this ON the VPS, from the project root:
#     cd /root/hyperoil
#     bash scripts/deploy_donchian_vps.sh
#
# Idempotent: safe to re-run. Aborts on the first error so you never end up
# with a half-deployed bot. Tails the first 30s of logs to confirm warmup +
# WebSocket connect succeeded before returning control.
#
# Pre-requisites that this script will NOT do for you:
#   1. SSH key auth set up (you should not be typing passwords here)
#   2. .env present at the project root with HYPERLIQUID_PRIVATE_KEY
#      → copy .env.donchian.template, fill in, chmod 600
#   3. data/donchian/*.parquet rsynced from your dev machine, OR you
#      accept that this script will run the (slow) collector itself
#   4. SQLite warmed from the parquets (persist_donchian_to_db.py)
# =============================================================================

set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/hyperoil}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/venv}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
LOG_FILE="$LOG_DIR/donchian_paper.jsonl"
PID_FILE="$PROJECT_DIR/donchian.pid"
HEALTH_PORT="${HEALTH_PORT:-9091}"

# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------
log()  { printf '\033[1;34m[deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[deploy WARN]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[deploy FAIL]\033[0m %s\n' "$*" >&2; exit 1; }

# ----------------------------------------------------------------------------
# Pre-flight checks
# ----------------------------------------------------------------------------
log "project dir: $PROJECT_DIR"
[[ -d "$PROJECT_DIR" ]]      || die "project dir not found: $PROJECT_DIR"
cd "$PROJECT_DIR"

[[ -f donchian_config.yaml ]] || die "donchian_config.yaml missing — git repo not synced?"
[[ -f .env ]]                 || die ".env missing — copy .env.donchian.template, fill it in"

# Refuse to run if .env is world-readable (private key inside)
ENV_PERMS=$(stat -c '%a' .env 2>/dev/null || stat -f '%A' .env)
if [[ "$ENV_PERMS" != "600" && "$ENV_PERMS" != "400" ]]; then
    warn ".env permissions are $ENV_PERMS — fixing to 600"
    chmod 600 .env
fi

if ! grep -q '^HYPERLIQUID_PRIVATE_KEY=0x' .env; then
    die ".env does not contain a HYPERLIQUID_PRIVATE_KEY=0x... line"
fi

# ----------------------------------------------------------------------------
# Stop any existing instance cleanly
# ----------------------------------------------------------------------------
if [[ -f "$PID_FILE" ]]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        log "stopping previous instance (pid=$OLD_PID) with SIGTERM"
        kill -TERM "$OLD_PID"
        for _ in {1..30}; do
            sleep 1
            kill -0 "$OLD_PID" 2>/dev/null || break
        done
        if kill -0 "$OLD_PID" 2>/dev/null; then
            warn "process did not exit after 30s, sending SIGKILL"
            kill -KILL "$OLD_PID" || true
        fi
    fi
    rm -f "$PID_FILE"
fi

# ----------------------------------------------------------------------------
# Pull latest code
# ----------------------------------------------------------------------------
log "git fetch + reset to origin/main"
git fetch --quiet origin main
LOCAL_DIRTY=$(git status --porcelain | grep -v '^??' || true)
if [[ -n "$LOCAL_DIRTY" ]]; then
    die "VPS working tree has local modifications — refusing to clobber:\n$LOCAL_DIRTY"
fi
git reset --hard origin/main
log "now at $(git rev-parse --short HEAD): $(git log -1 --pretty=%s)"

# ----------------------------------------------------------------------------
# Venv + deps
# ----------------------------------------------------------------------------
if [[ ! -d "$VENV_DIR" ]]; then
    log "creating venv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"

log "installing/upgrading project (editable)"
pip install --quiet --upgrade pip
pip install --quiet -e .

# ----------------------------------------------------------------------------
# Data: parquets + SQLite warmup
# ----------------------------------------------------------------------------
PARQUET_COUNT=$(find data/donchian -name '*.parquet' 2>/dev/null | wc -l)
if [[ "$PARQUET_COUNT" -lt 25 ]]; then
    warn "only $PARQUET_COUNT/25 parquets present — running collector (slow, ~10min)"
    python scripts/collect_donchian_data.py
    PARQUET_COUNT=$(find data/donchian -name '*.parquet' | wc -l)
    [[ "$PARQUET_COUNT" -ge 25 ]] || die "collector did not produce 25 parquets, got $PARQUET_COUNT"
fi
log "parquets present: $PARQUET_COUNT"

# Persist to SQLite (idempotent upsert)
log "persisting parquets to SQLite (idempotent)"
python scripts/persist_donchian_to_db.py

# ----------------------------------------------------------------------------
# Make sure the health port is free
# ----------------------------------------------------------------------------
if command -v ss >/dev/null 2>&1; then
    if ss -ltn "sport = :$HEALTH_PORT" | tail -n +2 | grep -q .; then
        die "port $HEALTH_PORT is already in use — another bot or stale process?"
    fi
fi

# ----------------------------------------------------------------------------
# Start
# ----------------------------------------------------------------------------
mkdir -p "$LOG_DIR"
log "starting donchian in paper mode → $LOG_FILE"
nohup python -m hyperoil --strategy donchian --mode paper \
    --log-level INFO --log-format json \
    >> "$LOG_FILE" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PID_FILE"
log "pid=$NEW_PID written to $PID_FILE"

# ----------------------------------------------------------------------------
# Sanity wait — confirm warmup + WS connect within 30s
# ----------------------------------------------------------------------------
log "waiting up to 30s for warmup + ws connect…"
DEADLINE=$((SECONDS + 30))
SAW_WARMUP=0
SAW_WS=0
while (( SECONDS < DEADLINE )); do
    if ! kill -0 "$NEW_PID" 2>/dev/null; then
        die "process died during startup — last 50 log lines:\n$(tail -50 "$LOG_FILE")"
    fi
    if (( SAW_WARMUP == 0 )) && grep -q '"donchian_orchestrator_started"' "$LOG_FILE" 2>/dev/null; then
        SAW_WARMUP=1
        log "  ✓ orchestrator started"
    fi
    if (( SAW_WS == 0 )) && grep -q '"ws_multi_connected"' "$LOG_FILE" 2>/dev/null; then
        SAW_WS=1
        log "  ✓ websocket connected to all symbols"
    fi
    if (( SAW_WARMUP == 1 && SAW_WS == 1 )); then
        break
    fi
    sleep 1
done

if (( SAW_WARMUP == 0 )); then
    die "did not see donchian_orchestrator_started in 30s — investigate $LOG_FILE"
fi
if (( SAW_WS == 0 )); then
    warn "warmup ok but ws_multi_connected not seen yet — may still be subscribing"
fi

# Health endpoint probe
if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 3 "http://127.0.0.1:$HEALTH_PORT/health" >/dev/null; then
        log "  ✓ health endpoint responding on :$HEALTH_PORT"
    else
        warn "health endpoint not responding — check manually: curl :$HEALTH_PORT/health"
    fi
fi

log "deploy complete — pid=$NEW_PID, log=$LOG_FILE"
log "next: bash scripts/check_donchian_health.sh    (run periodically)"
