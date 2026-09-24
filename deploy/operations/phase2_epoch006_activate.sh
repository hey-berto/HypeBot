#!/usr/bin/env bash
set -euo pipefail

: "${EXPECTED_OPERATIONS_SHA:?set the independently approved operations commit}"
RESEARCH_SHA=41e9e9acc261fd69d32c2d811e9ab4dd556c4620
CREATOR_SHA=d179a6a3b94d76adc05e8aaa1706153aa057c6cae5612c67cd61296d3b258f0a
DATABASE=/var/lib/hypebot/phase2/phase2_epoch_006.sqlite3
RECEIPT=/etc/hypebot/authorized/phase2-epoch-006.receipt.json
GRANT=/etc/hypebot/authorized/phase2-epoch-006.grant.json
READINESS_WINDOW_SECONDS=120
MONITOR_ALLOWANCE_SECONDS=10
artifacts_created=0

incident_stop() {
  status=$?
  trap - ERR
  if [[ "$artifacts_created" = 1 ]]; then
    systemctl stop hypebot-phase2-health.timer hypebot-phase2.service || true
    systemctl disable hypebot-phase2-health.timer hypebot-phase2.service || true
    echo "EPOCH006_ACTIVATION_INCIDENT_PRESERVED=yes" >&2
    echo "Do not delete, reset, retry, or reuse epoch006 artifacts." >&2
  fi
  exit "$status"
}
trap incident_stop ERR

finite_monotonic_timer_deadline() {
  local value
  value="$(systemctl show hypebot-phase2-health.timer \
    -p NextElapseUSecMonotonic --value)"
  /opt/hypebot/phase2/.venv/bin/python \
    /opt/hypebot/operations/deploy/operations/phase2_epoch006_activation_guard.py \
    --timer-deadline "$value" >/dev/null
}

test "$(id -u)" = 0
test "$(hostname)" = mmt2
test "$(git -C /opt/hypebot/phase2 rev-parse HEAD)" = "$RESEARCH_SHA"
test "$(git -C /opt/hypebot/operations rev-parse HEAD)" = "$EXPECTED_OPERATIONS_SHA"
test -z "$(git -C /opt/hypebot/phase2 status --porcelain --untracked-files=no)"
test -z "$(git -C /opt/hypebot/operations status --porcelain --untracked-files=no)"
test "$(timedatectl show -p NTPSynchronized --value)" = yes
test "$(systemctl is-active hypebot-phase2.service)" = inactive
test "$(systemctl is-enabled hypebot-phase2.service)" = disabled
test "$(systemctl is-active hypebot-phase2-health.timer)" = inactive
test "$(systemctl is-enabled hypebot-phase2-health.timer)" = disabled

for artifact in "$DATABASE" "$DATABASE-wal" "$DATABASE-shm" \
  "$RECEIPT" "$RECEIPT.consumed" "$GRANT"; do
  test ! -e "$artifact"
done

echo "$CREATOR_SHA  /opt/hypebot/operations/deploy/operations/phase2_epoch006_create_activation.py" \
  | sha256sum -c -
runuser -u hypebot -- /opt/hypebot/phase2/.venv/bin/python \
  /opt/hypebot/operations/deploy/operations/phase2_epoch006_start_gate.py \
  --network-only >/dev/null

/opt/hypebot/phase2/.venv/bin/python \
  /opt/hypebot/operations/deploy/operations/phase2_epoch006_create_activation.py
artifacts_created=1
/opt/hypebot/phase2/.venv/bin/hype-autopilot-phase2-authorize \
  --receipt "$RECEIPT" \
  --grant "$GRANT" \
  --grant-group hypebot-phase2-auth >/dev/null

test ! -e "$RECEIPT"
test -f "$RECEIPT.consumed"
test "$(stat -c '%U:%G:%a' "$GRANT")" = root:hypebot-phase2-auth:640
runuser -u hypebot -- test -r "$GRANT"
if runuser -u nobody -- test -r "$GRANT"; then
  echo "unauthorized user can read activation grant" >&2
  false
fi

prestart="$(sed -n 's/^ExecStartPre=//p' /etc/systemd/system/hypebot-phase2.service)"
runuser -u hypebot -- bash -c "$prestart" >/dev/null
systemctl enable --now hypebot-phase2.service
systemctl enable --now hypebot-phase2-health.timer

test "$(systemctl is-active hypebot-phase2.service)" = active
test "$(systemctl is-enabled hypebot-phase2.service)" = enabled
test "$(systemctl is-active hypebot-phase2-health.timer)" = active
test "$(systemctl is-enabled hypebot-phase2-health.timer)" = enabled
finite_monotonic_timer_deadline

main_pid="$(systemctl show hypebot-phase2.service -p MainPID --value)"
test "$main_pid" -gt 1
deadline=$((SECONDS + READINESS_WINDOW_SECONDS + MONITOR_ALLOWANCE_SECONDS))
ready=0
while (( SECONDS <= deadline )); do
  test "$(systemctl is-active hypebot-phase2.service)" = active
  test "$(systemctl show hypebot-phase2.service -p MainPID --value)" = "$main_pid"
  test "$(systemctl show hypebot-phase2.service -p NRestarts --value)" = 0
  read -r attempts anchors windows < <(
    /opt/hypebot/phase2/.venv/bin/python - "$DATABASE" <<'PY'
import sqlite3
import sys

db = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
attempts = db.execute(
    "SELECT COUNT(*) FROM phase2_recovery_events "
    "WHERE event_type='WORKER_START_ATTEMPT'"
).fetchone()[0]
anchors = db.execute(
    "SELECT COUNT(*) FROM phase2_recovery_events "
    "WHERE event_type='PROSPECTIVE_START_ESTABLISHED'"
).fetchone()[0]
windows = db.execute("SELECT COUNT(*) FROM phase2_evidence_windows").fetchone()[0]
db.close()
print(attempts, anchors, windows)
PY
  )
  test "$attempts" -le 1
  if [[ "$attempts" = 1 && "$anchors" = 1 && "$windows" = 1 ]]; then
    ready=1
    break
  fi
  if [[ "$attempts" = 1 ]] && ! pgrep -P "$main_pid" >/dev/null; then
    echo "worker exited before readiness completed" >&2
    false
  fi
  sleep 2
done
test "$ready" = 1
finite_monotonic_timer_deadline
echo "TIMER_NEXT_MONOTONIC=$(systemctl show hypebot-phase2-health.timer -p NextElapseUSecMonotonic --value)"
echo PHASE_2_EPOCH_006_ACTIVATION_STARTED_HEALTHY
