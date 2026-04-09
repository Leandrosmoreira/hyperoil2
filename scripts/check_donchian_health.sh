#!/usr/bin/env bash
# =============================================================================
# Donchian VPS health check — call periodically (or via cron) to verify the
# bot is alive, ingesting bars, and free of exceptions.
# =============================================================================
# Usage:
#     bash scripts/check_donchian_health.sh
#
# Exit codes:
#     0  all green
#     1  bot is down or has new exceptions in the log
#     2  warnings (no bars in last hour, ws disconnected, etc.)
# =============================================================================

set -uo pipefail

PROJECT_DIR="${PROJECT_DIR:-/root/hyperoil}"
LOG_FILE="${LOG_FILE:-$PROJECT_DIR/logs/donchian_paper.jsonl}"
PID_FILE="${PID_FILE:-$PROJECT_DIR/donchian.pid}"
HEALTH_PORT="${HEALTH_PORT:-9091}"

ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  ⚠\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m  ✗\033[0m %s\n' "$*"; }

EXIT_CODE=0
bump_warn() { (( EXIT_CODE < 2 )) && EXIT_CODE=2; }
bump_fail() { EXIT_CODE=1; }

cd "$PROJECT_DIR" 2>/dev/null || { fail "project dir missing: $PROJECT_DIR"; exit 1; }

echo "── donchian health $(date -u +%Y-%m-%dT%H:%M:%SZ) ──"

# ----------------------------------------------------------------------------
# 1. Process alive
# ----------------------------------------------------------------------------
if [[ ! -f "$PID_FILE" ]]; then
    fail "no PID file at $PID_FILE — bot was never started or stopped uncleanly"
    bump_fail
else
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        ok "process alive (pid=$PID)"
    else
        fail "PID file points to $PID but process is dead"
        bump_fail
    fi
fi

# ----------------------------------------------------------------------------
# 2. Health endpoint
# ----------------------------------------------------------------------------
if command -v curl >/dev/null 2>&1; then
    if HEALTH_BODY=$(curl -fsS --max-time 3 "http://127.0.0.1:$HEALTH_PORT/health" 2>/dev/null); then
        ok "health endpoint :$HEALTH_PORT responding"
    else
        fail "health endpoint :$HEALTH_PORT not responding"
        bump_fail
    fi
fi

# ----------------------------------------------------------------------------
# 3. Log file exists and is being written to
# ----------------------------------------------------------------------------
if [[ ! -f "$LOG_FILE" ]]; then
    fail "log file missing: $LOG_FILE"
    bump_fail
else
    LAST_MTIME=$(stat -c '%Y' "$LOG_FILE" 2>/dev/null || stat -f '%m' "$LOG_FILE")
    NOW=$(date +%s)
    AGE=$(( NOW - LAST_MTIME ))
    if (( AGE < 600 )); then
        ok "log file fresh (last write ${AGE}s ago)"
    elif (( AGE < 14400 )); then  # 4h = one bar
        warn "log file last written ${AGE}s ago (within 1 bar window — may be normal between candles)"
        bump_warn
    else
        fail "log file stale: ${AGE}s since last write"
        bump_fail
    fi
fi

# ----------------------------------------------------------------------------
# 4. Bar throughput in last 60 minutes
# ----------------------------------------------------------------------------
if [[ -f "$LOG_FILE" ]]; then
    SINCE=$(date -u -d '60 minutes ago' +%Y-%m-%dT%H:%M 2>/dev/null \
        || date -u -v-60M +%Y-%m-%dT%H:%M)
    TICK_COUNT=$(awk -v since="$SINCE" '
        /donchian_pipeline_tick/ && $0 >= "{\"timestamp\": \""since {c++}
        END {print c+0}
    ' "$LOG_FILE")
    BAR_COUNT=$(awk -v since="$SINCE" '
        /donchian_bar_persisted/ && $0 >= "{\"timestamp\": \""since {c++}
        END {print c+0}
    ' "$LOG_FILE")
    ok "last 60min: $BAR_COUNT bars persisted, $TICK_COUNT pipeline ticks"
    if (( BAR_COUNT == 0 )); then
        warn "no bars in the last hour — normal if mid-bar, suspicious if persistent"
        bump_warn
    fi
fi

# ----------------------------------------------------------------------------
# 5. Exceptions / fatal errors anywhere in the log
# ----------------------------------------------------------------------------
if [[ -f "$LOG_FILE" ]]; then
    EXC_COUNT=$(grep -cE 'fatal_error|"exception"|Traceback' "$LOG_FILE" 2>/dev/null || echo 0)
    if (( EXC_COUNT == 0 )); then
        ok "no exceptions in log"
    else
        fail "$EXC_COUNT exception/fatal_error/traceback lines in log"
        bump_fail
        echo "    last 3 problematic lines:"
        grep -E 'fatal_error|"exception"|Traceback' "$LOG_FILE" | tail -3 \
            | sed 's/^/      /'
    fi
fi

# ----------------------------------------------------------------------------
# 6. Latest WS state
# ----------------------------------------------------------------------------
if [[ -f "$LOG_FILE" ]]; then
    LAST_WS=$(grep -o '"ws_multi_state"[^}]*"new":"[a-z]*"' "$LOG_FILE" | tail -1 | grep -o '"new":"[a-z]*"' || true)
    if [[ "$LAST_WS" == *connected* ]]; then
        ok "ws state: connected"
    elif [[ -n "$LAST_WS" ]]; then
        warn "ws state: $LAST_WS"
        bump_warn
    fi
fi

# ----------------------------------------------------------------------------
# 7. Open positions count (just informational)
# ----------------------------------------------------------------------------
if [[ -f "$LOG_FILE" ]]; then
    LAST_TICK=$(grep '"donchian_pipeline_tick"' "$LOG_FILE" | tail -1)
    if [[ -n "$LAST_TICK" ]]; then
        N_POS=$(echo "$LAST_TICK" | grep -o '"n_positions":[0-9]*' | cut -d: -f2)
        [[ -n "$N_POS" ]] && ok "open positions (last tick): $N_POS"
    fi
fi

echo
case $EXIT_CODE in
    0) printf '\033[1;32mGREEN\033[0m — all checks passed\n' ;;
    2) printf '\033[1;33mYELLOW\033[0m — warnings present, monitor closely\n' ;;
    1) printf '\033[1;31mRED\033[0m — failures detected, investigate immediately\n' ;;
esac
exit $EXIT_CODE
