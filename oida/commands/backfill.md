# /oida backfill (Codex)

Send past Codex sessions to OIDA. **Dry-run first; never push without confirming.**

The scan is not time-incremental — it enumerates *all* rollout files and dedups via the
ledger — so a normal run already backfills. To do it deliberately:

1. **Dry-run (always first).** Builds the plan and logs what WOULD be sent, no push:

   ```sh
   OIDA_DRY_RUN=1 bash oida/hooks/scan.sh
   tail -n 50 ~/.oida/work/capture-codex.log      # review the planned sessions
   ```

2. **Confirm**, then push:

   ```sh
   bash oida/hooks/scan.sh
   ```

Only allowlisted repos appear in the plan; the planner drops repo-less and undesignated
sessions, and files whose schema it can't read go to the skip queue. The server is
idempotent, so a re-run is safe.
