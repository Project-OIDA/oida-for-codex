# /oida status (Codex)

Show OIDA Codex-client status. Never print the device key.

- **Configured?** does `~/.oida/config.json` exist? Show `apiUrl` only.
- **Paused?** does `~/.oida/PAUSED` exist? (toggle with pause, below)
- **Last capture run:** mtime of `~/.oida/work/.last-run-codex`; tail `~/.oida/work/capture-codex.log`.
- **Pushed so far:** number of entries in `~/.oida/work/ledger.json`.
- **Allowlisted repos (cached):** the `repos` in `~/.oida/work/allowlist.json`.
- **Pending (dry-run, does NOT push):**

  ```sh
  OIDA_DRY_RUN=1 bash oida/hooks/scan.sh && tail ~/.oida/work/capture-codex.log
  ```

The ledger and allowlist cache are shared with the Claude client; the `capture-codex.log`
and `.last-run-codex` marker are Codex-specific.
