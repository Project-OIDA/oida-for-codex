# /oida install (Codex)

Set up OIDA session capture for Codex CLI on this machine.

```sh
./install.sh [DEVICE_KEY] [--api-url URL] [--wire-notify]
```

1. **Device key** — pass it as the first argument, or the installer prompts for it. Mint it in
   OIDA → Settings → "OIDA for Claude" (shown once, looks like `oida_sess_…`). The same key works
   for both the Claude and Codex clients; if `~/.oida/config.json` already exists the installer reuses it.
2. **API URL** — defaults to the OIDA production host; override with `--api-url`.
3. The installer writes `~/.oida/config.json` (mode 600), verifies the key against
   `/ingest/sessions/allowlist` (aborts on 401 — nothing is installed), and registers an hourly
   timer (launchd on macOS, `systemd --user` on Linux).
4. **Optional:** `--wire-notify` also fires capture right after each Codex turn by adding a
   top-level `notify` entry to `~/.codex/config.toml` (backed up first; skipped if you already set one).
   The hourly timer already captures without it.

Only sessions in repos your workspace has designated are ever sent; everything else is dropped
locally and again server-side. Never print the full device key back to a transcript.
