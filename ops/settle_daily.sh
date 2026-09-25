#!/bin/bash
# Нічний settle M1 + нативний D1 у денну перерву (ADR-0103 §3.2, S3d) — обгортка tools.repair.settle_daily.
#   cron (root, ops/smc-settle-daily.cron): /opt/smc-v3/ops/settle_daily.sh --scheduled
#   ручний прогін (S4, кожен — за «го» власника): sudo /opt/smc-v3/ops/settle_daily.sh --manual
#   репетиція без запису: sudo /opt/smc-v3/ops/settle_daily.sh --manual --dry-run [--ignore-break]
# Рейки обгортки поверх рейок оркестратора:
#   flock — один прогін за раз (cron і ручний не перетинаються);
#   timeout — стеля 45 хв (перерва 60 хв − запас); SIGTERM не дає Python виконати finally, тому
#   trap — якщо записувачів зупинив прогін (маркер у work_dir) і він загинув, стартує їх у порядку проду.
# Вивід — /var/log/smc-v3/settle_daily.log (logrotate smc-v3); збій — ще й syslog (logger -t smc-settle).
set -uo pipefail
P=${SMC_V3_DIR:-/opt/smc-v3}  # перевизначення шляхів — лише для тесту обгортки (tests/test_settle_daily_wrapper.py)
LOG=${SETTLE_DAILY_LOG:-/var/log/smc-v3/settle_daily.log}
LOCK=${SETTLE_DAILY_LOCK:-/run/lock/smc-settle-daily.lock}
PRIME_PAUSE_S=${SETTLE_TRAP_PRIME_S:-15}  # PRIME інжесту (~10 с) до старту читачів — порядок проду
CEILING_S=2700
PY=$P/.venv/bin/python
WORK_DIR=$("$PY" -c "import json,sys; print(json.load(open(sys.argv[1]))['m1_settle']['work_dir'])" "$P/config.json") \
  || { logger -t smc-settle -p user.err "SETTLE_DAILY_CONFIG_UNREADABLE"; exit 2; }
MARKER=$WORK_DIR/writers_stopped_by_settle

exec 9>"$LOCK"
flock -n 9 || { logger -t smc-settle "SETTLE_DAILY_LOCKED — інший прогін триває"; exit 0; }

restart_if_stopped_by_run() {
  [ -f "$MARKER" ] || return 0
  logger -t smc-settle -p user.err "SETTLE_DAILY_TRAP оркестратор загинув із зупиненими записувачами — старт"
  supervisorctl start smc:smc-fxcm
  sleep "$PRIME_PAUSE_S"
  supervisorctl start smc:smc-preview smc:smc-ws
  rm -f "$MARKER"
}
trap restart_if_stopped_by_run EXIT

cd "$P" || exit 2
timeout -k 30 "$CEILING_S" "$PY" -m tools.repair.settle_daily "$@" >> "$LOG" 2>&1
RC=$?
[ "$RC" -eq 0 ] || logger -t smc-settle -p user.err \
  "SETTLE_DAILY_RC=$RC — $LOG, $WORK_DIR/last_status.json (3 — дані не змінено, 4 — після старту, 5 — відкат, 6 — відкат не вдався)"
exit "$RC"
