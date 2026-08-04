# What OIDA for Codex captures — and your consent

Plain-language summary for the developer installing this client. Your organization's formal
privacy notice (the *informativa*) is the authoritative document — ask your workspace admin
for it. This page explains what happens on **your** machine and the choices you control. Same
privacy model as [`oida-for-claude`](../../oida-for-claude); only the transcript format differs.

## Consent: mint your own key

`./install.sh` asks for a **device key** you minted in OIDA → Settings → "OIDA for Claude"
(the same key type works for both clients). At mint time you tick a consent statement (version
`2026-07-22`) and that acknowledgement is recorded against **your** account.

> **Mint your *own* key.** The recorded consent belongs to whoever mints the key. So the record
> reflects *your* awareness, **mint the key yourself** — don't reuse a key someone else minted
> for you. If you can't reach the mint screen, ask your admin for access rather than borrowing
> a key.

## What is captured (only for repos your workspace designates)

- **Every user/assistant turn**, redacted on your machine before anything is sent.
- A **tool-call log**: the tool name + a redacted, truncated input.
- **Metrics**: session start/end, active time, git line stats (+/−), commit messages, branch.

## What is **not** captured

- **Tool outputs / results** and the model's **internal/encrypted reasoning**: dropped on your
  machine, never sent (the biggest secret surface).
- Anything in a repo **not on your workspace's allowlist** — dropped locally *and* re-checked
  server-side.
- Anything **outside working hours** (default: no capture 20:00–08:00 or weekends;
  timezone/policy set by your workspace).
- Anything while **paused**.

## Redaction is best-effort — don't paste live secrets

Every string that leaves your machine — turn text, tool-call inputs, **commit subjects** and
the **git remote URL** — is passed through a local pattern-based redactor (API keys, tokens,
connection strings, credentialed remotes) first. **It is not exhaustive.** Never paste live
secrets into prompts — use a secret manager. If a secret slips through, use **erase** (below)
and tell your admin.

## Your controls

| You want to… | Do this | Effect |
|---|---|---|
| Pause capture | `touch ~/.oida/PAUSED` (resume: `rm ~/.oida/PAUSED`), or the workspace toggle | Stops immediately |
| Rotate your key | Settings → "Rotate", then re-run `./install.sh <new-key>` | Old key stops working |
| Withdraw consent | Settings → "Withdraw my consent & revoke my keys" | All your keys revoked, future capture stops. **Already-stored knowledge is *not* deleted by this** — use erase |
| Erase a session's data | Per-session erase / ask your admin | Deletes that session's stored transcript + derived knowledge |
| Stop entirely | `./install.sh --uninstall` (removes the timer) | No further scans |

Raw captured session content ages out automatically on the workspace retention window. Derived organizational learning records do **not** age out merely because the source reaches that window; an explicit session/source erasure propagates to frozen copies and derived stores while retaining only justified content-free audit markers.

## How it stays private

- **Deterministic client**: no LLM and no API keys run on your machine — it only captures,
  redacts, and sends. All extraction happens server-side.
- **Allowlist, default deny**: only designated repos are ever captured; the server re-checks,
  so a misconfigured client cannot bypass it.
- **Show-once keys**: only a one-way hash is stored server-side; revoke or rotate anytime.
- **No individual evaluation**: OIDA has no per-person analytics or ranking — your git email is
  used only for provenance and to let you erase your own data.

See the repo `README.md` for install/ops and `docs/SCHEMA.md` for exactly which rollout fields
are read.
