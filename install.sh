#!/usr/bin/env bash
# OIDA for Codex — installer.
#
#   ./install.sh [DEVICE_KEY] [--api-url URL] [--wire-notify] [--uninstall]
#
# Sets up deterministic, out-of-band capture of your Codex CLI sessions:
#   1. writes ~/.oida/config.json (device key + API URL) — SHARED with oida-for-claude
#   2. verifies the key against the server's allowlist endpoint
#   3. installs an hourly timer (launchd on macOS, systemd --user on Linux)
#   4. optionally wires the Codex `notify` hook so capture also fires after a turn
#
# No secrets and no LLM ever run on this machine; the device key is minted per-user
# in OIDA → Settings → "OIDA for Claude" and only authorizes push. Only sessions in
# repos your workspace has designated are sent — everything else is dropped locally
# and again server-side.
set -euo pipefail

# <!-- operator: this is the OIDA production API host. -->
DEFAULT_API_URL="https://oida-api.onrender.com"

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
OIDA_DIR="${REPO_DIR}/oida"
OIDA_HOME="${HOME}/.oida"
CONFIG="${OIDA_HOME}/config.json"
PLIST_LABEL="com.oida.codex.capture"
PLIST_DEST="${HOME}/Library/LaunchAgents/${PLIST_LABEL}.plist"
SYSTEMD_DIR="${HOME}/.config/systemd/user"

DEVICE_KEY=""
API_URL=""
WIRE_NOTIFY=0
UNINSTALL=0
while [ $# -gt 0 ]; do
  case "$1" in
    --api-url) API_URL="${2:-}"; shift 2 ;;
    --wire-notify) WIRE_NOTIFY=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) DEVICE_KEY="$1"; shift ;;
  esac
done

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Render a trigger template: literal-replace __KEY__ tokens via python (robust to
# any path characters — a repo cloned to a path containing sed's delimiter `|` or
# the replacement metachar `&` would otherwise corrupt the generated plist/unit).
render_tmpl() {  # render_tmpl SRC DEST KEY1 VAL1 [KEY2 VAL2 ...]
  local src="$1" dest="$2"; shift 2
  python3 - "$src" "$dest" "$@" <<'PY'
import sys
src, dest, pairs = sys.argv[1], sys.argv[2], sys.argv[3:]
with open(src, encoding="utf-8") as f:
    s = f.read()
for i in range(0, len(pairs) - 1, 2):
    s = s.replace(pairs[i], pairs[i + 1])
with open(dest, "w", encoding="utf-8") as f:
    f.write(s)
PY
}

# ── uninstall ───────────────────────────────────────────────────────────────
if [ "${UNINSTALL}" = "1" ]; then
  case "$(uname -s)" in
    Darwin) launchctl unload "${PLIST_DEST}" 2>/dev/null || true; rm -f "${PLIST_DEST}"; say "removed launchd agent" ;;
    Linux)  systemctl --user disable --now oida-codex.timer 2>/dev/null || true
            rm -f "${SYSTEMD_DIR}/oida-codex.service" "${SYSTEMD_DIR}/oida-codex.timer"
            systemctl --user daemon-reload 2>/dev/null || true; say "removed systemd timer" ;;
  esac
  say "capture disabled. Config left at ${CONFIG} (delete it manually to fully remove)."
  exit 0
fi

command -v python3 >/dev/null 2>&1 || die "python3 is required"
command -v curl >/dev/null 2>&1 || die "curl is required"

# ── 1. config (reuse existing shared config where possible) ───────────────────
mkdir -p "${OIDA_HOME}"; chmod 700 "${OIDA_HOME}" 2>/dev/null || true
existing_key=""; existing_url=""
if [ -f "${CONFIG}" ]; then
  existing_key="$(python3 -c "import json;print(json.load(open('${CONFIG}')).get('deviceKey',''))" 2>/dev/null || echo "")"
  existing_url="$(python3 -c "import json;print(json.load(open('${CONFIG}')).get('apiUrl',''))" 2>/dev/null || echo "")"
fi
[ -z "${DEVICE_KEY}" ] && DEVICE_KEY="${existing_key}"
[ -z "${API_URL}" ] && API_URL="${existing_url:-${DEFAULT_API_URL}}"
if [ -z "${DEVICE_KEY}" ]; then
  printf 'Paste your OIDA device key (from Settings → "OIDA for Claude", looks like oida_sess_…): '
  read -r DEVICE_KEY </dev/tty
fi
[ -n "${DEVICE_KEY}" ] || die "no device key provided"

umask 077
python3 - "$CONFIG" "$API_URL" "$DEVICE_KEY" <<'PY'
import json, sys
path, url, key = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    cfg = json.load(open(path))
except Exception:
    cfg = {}
cfg["apiUrl"] = url.rstrip("/")
cfg["deviceKey"] = key
json.dump(cfg, open(path, "w"), indent=2)
PY
chmod 600 "${CONFIG}"
say "wrote ${CONFIG} (apiUrl=${API_URL})"

# ── 2. verify the key ─────────────────────────────────────────────────────────
say "verifying device key against ${API_URL}/ingest/sessions/allowlist …"
if ! resp="$(curl -fsS -H "Authorization: Bearer ${DEVICE_KEY}" "${API_URL%/}/ingest/sessions/allowlist" 2>/dev/null)"; then
  die "key/URL verification failed (401/404/network). Fix the key or --api-url and re-run; capture NOT installed."
fi
repos="$(printf '%s' "${resp}" | python3 -c "import json,sys;d=json.load(sys.stdin);print(', '.join(d.get('repos',[])) or '(none yet — designate repos in OIDA Settings)')" 2>/dev/null || echo '?')"
say "  ✓ key valid. Designated repos: ${repos}"

# ── 3. install the timer ──────────────────────────────────────────────────────
case "$(uname -s)" in
  Darwin)
    mkdir -p "${HOME}/Library/LaunchAgents"
    render_tmpl "${OIDA_DIR}/triggers/${PLIST_LABEL}.plist" "${PLIST_DEST}" \
      __OIDA_DIR__ "${OIDA_DIR}" __HOME__ "${HOME}"
    launchctl unload "${PLIST_DEST}" 2>/dev/null || true
    launchctl load -w "${PLIST_DEST}"
    say "  ✓ installed launchd agent ${PLIST_LABEL} (hourly + at login)"
    ;;
  Linux)
    mkdir -p "${SYSTEMD_DIR}"
    render_tmpl "${OIDA_DIR}/triggers/oida-codex.service" "${SYSTEMD_DIR}/oida-codex.service" __OIDA_DIR__ "${OIDA_DIR}"
    cp "${OIDA_DIR}/triggers/oida-codex.timer" "${SYSTEMD_DIR}/oida-codex.timer"
    systemctl --user daemon-reload
    systemctl --user enable --now oida-codex.timer
    say "  ✓ installed systemd --user timer oida-codex.timer (hourly + after boot)"
    ;;
  *) say "  ! unknown OS ($(uname -s)); run 'bash ${OIDA_DIR}/hooks/scan.sh' from cron yourself." ;;
esac

# ── 4. optional notify hook (accelerator; the timer already covers capture) ────
CODEX_TOML="${HOME}/.codex/config.toml"
NOTIFY_LINE="notify = [\"${OIDA_DIR}/hooks/notify.sh\"]"
if [ "${WIRE_NOTIFY}" = "1" ]; then
  if [ -f "${CODEX_TOML}" ] && grep -qE '^[[:space:]]*notify[[:space:]]*=' "${CODEX_TOML}"; then
    say "  ! ${CODEX_TOML} already sets 'notify'; leaving it untouched. Add manually if you want OIDA to also fire on notify:"
    say "      ${NOTIFY_LINE}"
  else
    # Insert as a TOP-LEVEL key: before the first [section] header (a bare key
    # appended at EOF would bind to the last table, not the document root).
    [ -f "${CODEX_TOML}" ] && cp "${CODEX_TOML}" "${CODEX_TOML}.oida-bak.$(date +%s)"
    mkdir -p "${HOME}/.codex"; touch "${CODEX_TOML}"
    awk -v line="${NOTIFY_LINE}" '
      BEGIN{done=0}
      /^[[:space:]]*\[/ && !done {print line; done=1}
      {print}
      END{if(!done) print line}' "${CODEX_TOML}" > "${CODEX_TOML}.tmp" && mv "${CODEX_TOML}.tmp" "${CODEX_TOML}"
    say "  ✓ wired Codex notify -> ${OIDA_DIR}/hooks/notify.sh (backup saved)"
  fi
else
  say ""
  say "  (optional) to also capture right after each turn, add this TOP-LEVEL line to ${CODEX_TOML}:"
  say "      ${NOTIFY_LINE}"
  say "  or re-run with --wire-notify. The hourly timer already captures without it."
fi

say ""
say "Done. Capture runs automatically. Logs: ${OIDA_HOME}/work/capture-codex.log"
say "  pause:    touch ${OIDA_HOME}/PAUSED        (resume: rm ${OIDA_HOME}/PAUSED)"
say "  backfill: OIDA_DRY_RUN=1 bash ${OIDA_DIR}/hooks/scan.sh   (preview, then run without OIDA_DRY_RUN)"
say "  status:   bash ${OIDA_DIR}/hooks/scan.sh && tail ${OIDA_HOME}/work/capture-codex.log"
