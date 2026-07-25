# OIDA for Codex

Deterministic, out-of-band capture of your **Codex CLI** coding sessions for
[OIDA](https://github.com/Project-OIDA). The sibling of
[`oida-for-claude`](../oida-for-claude) — same wire contract, same privacy model,
same server; only the on-disk transcript format (and therefore the parser + the
trigger) differ, because Codex is not a Claude plugin.

## What it does

A timer (launchd on macOS, `systemd --user` on Linux) scans `~/.codex/sessions`
about once an hour. For each **new, quiescent, allowlisted** session it:

1. parses the rollout JSONL into a compact transcript (`engine/transcript.py`),
2. redacts every string locally (`engine/redact.py`),
3. builds a `SessionEnvelope` and gzip-POSTs it to `POST /ingest/sessions`
   with your device bearer (`lib/push.py`).

**No LLM and no API keys ever run on this machine.** All extraction happens
server-side in OIDA. The only credential here is the OIDA device key, which only
authorizes push. Only sessions in repos your workspace has **designated** are sent
— everything else is dropped locally *and* re-checked server-side.

### Deliberate reduction

Captured: every user/assistant turn verbatim (redacted) + a tool-call **log**
(tool name + redacted, truncated input) + git/time metrics. **Not** captured: tool
**result** bodies (~90% of bytes, the biggest secret surface) and the model's
encrypted reasoning. See [`docs/SCHEMA.md`](docs/SCHEMA.md).

## Install

```sh
git clone https://github.com/Project-OIDA/oida-for-codex.git
cd oida-for-codex
./install.sh                 # prompts for your device key; --api-url to override the host
```

Get the device key from OIDA → Settings → "OIDA for Claude" (it works for both
clients) — **mint your own key** so the recorded consent is yours. `./install.sh
--wire-notify` also fires capture right after each Codex turn; the hourly timer
already covers capture without it.

**Before installing, read [`docs/CONSENT.md`](docs/CONSENT.md)** — what is (and isn't)
captured and the controls you have (pause / rotate / withdraw / erase).

Operations:

| action | command |
|---|---|
| pause | `touch ~/.oida/PAUSED` (resume: `rm ~/.oida/PAUSED`) |
| backfill past sessions | `OIDA_DRY_RUN=1 bash oida/hooks/scan.sh` (preview), then without the flag |
| status / logs | `tail ~/.oida/work/capture-codex.log` |
| uninstall | `./install.sh --uninstall` |

Config lives in `~/.oida/config.json` (`{apiUrl, deviceKey}`, mode 600). Optional
`gitHosts: ["git.acme.dev"]` extends the hosts whose `owner/repo` may match a
host-less allowlist entry — the default is `github.com` alone, so a same-named
repo on another host is not captured.

See [`oida/commands/`](oida/commands) for the details of each.

## Layout

```
install.sh                     one-shot installer (config · verify · timer · notify)
oida/
  engine/
    transcript.py   Codex rollout JSONL -> {turns, tool_calls}   (Codex-specific)
    sources.py      ~/.codex/sessions discovery (cross-OS/WSL)   (Codex-specific)
    extract.py      enumeration · ledger · skip queue            (mostly shared logic)
    redact.py       local secret scrub          ⚠ SHARED — sync with oida-for-claude
    metrics.py      git line-stats · active time ⚠ SHARED — sync with oida-for-claude
  lib/
    plan.py         deterministic planner (new · quiescent · allowlisted)
    push.py         build SessionEnvelope, gzip POST, ledger     (surface = codex_cli)
  hooks/
    scan.sh         the guarded scan (timer + notify both call this)
    notify.sh       Codex `notify` hook -> triggers a scan
  triggers/         launchd plist + systemd service/timer templates
  commands/         install · status · backfill · pause (docs)
docs/SCHEMA.md      verified Codex rollout schema (re-verify before each release)
```

## Shared code with oida-for-claude

`engine/redact.py` and `engine/metrics.py` are **byte-for-byte copies** of the
Claude client's, and `extract.py`/`plan.py`/`push.py` share most of their logic.
This is deliberate duplication, not a submodule: a customer-installed client must
stay self-contained and installable without `--recurse-submodules`. If you change
a redaction pattern or a metric here, change it in `oida-for-claude` too (both
files carry a `⚠ SHARED CODE` header).

## Self-tests

Every engine module runs offline:

```sh
for m in oida/engine/*.py; do python3 "$m" --self-test; done
```
