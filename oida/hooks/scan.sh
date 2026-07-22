#!/usr/bin/env bash
# OIDA for Codex — deterministic, out-of-band session capture.
#
# Codex has no plugin-hook system like Claude Code's SessionEnd, so capture is
# SCAN-BASED: a launchd/systemd timer runs this every ~hour, and the optional
# `notify` hook (hooks/notify.sh, wired into ~/.codex/config.toml) fires it sooner
# after a turn. Either way the work is identical and idempotent. Like the Claude
# client there is NO LLM step — the client only captures, redacts and pushes; all
# extraction happens server-side in OIDA. Flow:
#   1. cheap guards (recursion / pause / throttle / config present / "anything new?")
#   2. plan.py  — enumerate new · quiescent · allowlisted rollouts               [~1s, no LLM]
#   3. plan empty -> done (the common case does no network I/O)
#   4. push.py  — build the SessionEnvelope per session, gzip POST, record in the ledger
#
# Always exits 0 (fire-and-forget). The push child sets OIDA_CAPTURE=1 so a
# notify that fires mid-capture bails immediately.

set -u

OIDA_HOME="${HOME}/.oida"
CONFIG="${OIDA_HOME}/config.json"
WORK="${OIDA_HOME}/work"
MARKER="${WORK}/.last-run-codex"
LOG="${WORK}/capture-codex.log"
PLAN="${WORK}/plan-codex.json"
SESSIONS_ROOT="${HOME}/.codex/sessions"
MIN_INTERVAL_SEC="${OIDA_MIN_INTERVAL_SEC:-3600}"

# ── guards ────────────────────────────────────────────────────────────────────
[ -n "${OIDA_CAPTURE:-}" ] && exit 0                # recursion guard (we are the push child)
[ -f "${OIDA_HOME}/PAUSED" ] && exit 0              # local opt-out (/oida:pause)
[ -f "${CONFIG}" ] || exit 0                        # not installed yet (install.sh)
command -v python3 >/dev/null 2>&1 || exit 0

mkdir -p "${WORK}" 2>/dev/null || exit 0

# Min-interval throttle (protects the per-turn notify path from over-running).
if [ -f "${MARKER}" ]; then
  last="$(stat -c %Y "${MARKER}" 2>/dev/null || stat -f %m "${MARKER}" 2>/dev/null || echo 0)"
  now="$(date +%s)"
  [ $((now - last)) -lt "${MIN_INTERVAL_SEC}" ] && exit 0

  # Cheap "anything new?" probe — a rollout touched since the last run.
  newer="$(find "${SESSIONS_ROOT}" -type f -name 'rollout-*.jsonl' -newer "${MARKER}" -print -quit 2>/dev/null)"
  [ -z "${newer}" ] && exit 0
fi
touch "${MARKER}" 2>/dev/null                       # claim this run before doing work

# ── Server-authoritative pause + off-hours (Phase D3), local fallback ──────────
# Curl the device-authed settings endpoint (source of truth). If unreachable, fall
# back to local off-hours enforcement unless opted all-hours. Off-hours = outside
# Mon–Fri 08:00–20:00 (diritto alla disconnessione). Ported from control8/auto-eod.sh.
_offhours() { [ "$1" -gt 5 ] || [ "$2" -lt 8 ] || [ "$2" -ge 20 ]; }   # $1=dow(1-7) $2=hr(0-23)
API_URL="$(python3 -c "import json;print(json.load(open('${CONFIG}')).get('apiUrl',''))" 2>/dev/null)"
DEVICE_KEY="$(python3 -c "import json;print(json.load(open('${CONFIG}')).get('deviceKey',''))" 2>/dev/null)"
S_JSON=""
if command -v curl >/dev/null 2>&1 && [ -n "${API_URL}" ] && [ -n "${DEVICE_KEY}" ]; then
  S_JSON="$(curl -fsS --max-time 4 -H "Authorization: Bearer ${DEVICE_KEY}" "${API_URL%/}/ingest/sessions/settings" 2>/dev/null)"
fi
if [ -n "${S_JSON}" ]; then                          # server reachable = source of truth
  read -r S_PAUSED S_ENFORCE S_TZ <<< "$(printf '%s' "${S_JSON}" | python3 -c 'import json,sys
try: d=json.load(sys.stdin)
except Exception: print("0 1 Europe/Rome"); sys.exit(0)
print("%d %d %s" % (1 if d.get("paused") else 0, 0 if d.get("enforceOffHours") is False else 1, d.get("timezone") or "Europe/Rome"))' 2>/dev/null)"
  [ "${S_PAUSED}" = "1" ] && { echo "$(date): skip — paused via server settings" >>"${LOG}"; exit 0; }
  if [ "${S_ENFORCE}" != "0" ]; then
    dow="$(TZ="${S_TZ:-Europe/Rome}" date +%u)"; hr="$((10#$(TZ="${S_TZ:-Europe/Rome}" date +%H)))"
    _offhours "${dow}" "${hr}" && { echo "$(date): skip — off-hours (${S_TZ:-Europe/Rome})" >>"${LOG}"; exit 0; }
  fi
elif [ ! -f "${OIDA_HOME}/TRACK_ALL_HOURS" ] && [ "${OIDA_ALL_HOURS:-0}" != "1" ]; then
  dow="$(date +%u)"; hr="$((10#$(date +%H)))"          # server unreachable -> local off-hours fallback
  _offhours "${dow}" "${hr}" && { echo "$(date): skip — off-hours (local fallback)" >>"${LOG}"; exit 0; }
fi

OIDA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)"

# ── 2. deterministic plan (no LLM, no network except the cached allowlist) ──────
if ! OIDA_CONFIG="${CONFIG}" python3 "${OIDA_DIR}/lib/plan.py" --out "${PLAN}" --work "${WORK}" >>"${LOG}" 2>&1; then
  echo "$(date): plan failed" >>"${LOG}"; exit 0
fi
count="$(python3 -c "import json,sys;print(len(json.load(open('${PLAN}'))))" 2>/dev/null || echo 0)"
echo "$(date): plan -> ${count} session(s) to push" >>"${LOG}"
[ "${count}" = "0" ] && exit 0

# ── 3. dry-run: log the plan, do not push ───────────────────────────────────────
if [ -n "${OIDA_DRY_RUN:-}" ]; then
  echo "$(date): DRY-RUN — would push:" >>"${LOG}"; cat "${PLAN}" >>"${LOG}"; exit 0
fi

# ── 4. push (deterministic), fully detached ─────────────────────────────────────
if command -v setsid >/dev/null 2>&1; then
  OIDA_CAPTURE=1 setsid python3 "${OIDA_DIR}/lib/push.py" --plan "${PLAN}" --work "${WORK}" </dev/null >>"${LOG}" 2>&1 &
else
  OIDA_CAPTURE=1 nohup python3 "${OIDA_DIR}/lib/push.py" --plan "${PLAN}" --work "${WORK}" </dev/null >>"${LOG}" 2>&1 &
fi

exit 0
