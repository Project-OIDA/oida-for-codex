# /oida pause (Codex)

Pause or resume OIDA session capture on this machine. Local, machine-level switch —
shared with the Claude client (one `PAUSED` file pauses both).

- **Pause:** `mkdir -p ~/.oida && touch ~/.oida/PAUSED` — nothing is captured or sent while it exists.
- **Resume:** `rm -f ~/.oida/PAUSED` — capture runs again at the next timer tick.

Org-wide pause / off-hours ("diritto alla disconnessione") are enforced server-side in a later
phase; this file is the local opt-out.
