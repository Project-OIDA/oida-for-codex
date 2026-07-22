#!/usr/bin/env bash
# OIDA for Codex — optional `notify` hook.
#
# Wired into ~/.codex/config.toml as:  notify = ["/abs/path/to/oida/hooks/notify.sh"]
# Codex invokes it after events (e.g. turn/agent completion) with a single JSON
# argument describing the event. We DELIBERATELY ignore the argument's shape — the
# notify payload format is not stable across Codex versions, so treating "notify
# fired" as nothing more than "trigger a scan" keeps this robust to schema drift.
# The scan itself is throttled + quiescence-gated, so firing per-turn is cheap and
# never captures a still-active session.
#
# Fire-and-forget: detach the scan and return immediately so Codex is never blocked.
set -u

OIDA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd)"
SCAN="${OIDA_DIR}/hooks/scan.sh"
[ -x "${SCAN}" ] || [ -f "${SCAN}" ] || exit 0

if command -v setsid >/dev/null 2>&1; then
  setsid bash "${SCAN}" </dev/null >/dev/null 2>&1 &
else
  nohup bash "${SCAN}" </dev/null >/dev/null 2>&1 &
fi
exit 0
