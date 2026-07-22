#!/usr/bin/env python3
"""OIDA for Codex — deterministic session metrics.

⚠️  SHARED CODE — the executable body below is kept byte-identical with
    oida-for-claude/oida/engine/metrics.py; only this docstring differs intentionally.
    Surface-agnostic (git line stats + timestamp math, independent of transcript
    format). Duplicated rather than submoduled so each client repo stays
    self-contained and installable. If you change it here, change it there too.

git line stats for the session's commit window, active (hands-on) time with capped
idle gaps, and commit messages. All pure/defensive — a missing git, a non-repo dir,
or an unparseable timestamp degrades to empty, never raises. These land in the
envelope's `transcript.metrics` and `transcript.git_stats` (quarantined in
`events.payload` server-side).
"""
import os
import subprocess
import sys
from datetime import datetime, timezone

GAP_CAP_SEC = 300  # a pause longer than 5 min counts as 5 min of active time.


def _parse_ts(s):
    if not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def active_time(timestamps):
    """Hands-on seconds (sum of inter-turn gaps, each capped at GAP_CAP_SEC) and
    the raw wall span, from an iterable of ISO timestamps."""
    times = sorted(t for t in (_parse_ts(x) for x in timestamps) if t is not None)
    if len(times) < 2:
        return {"active_sec": 0, "wall_sec": 0}
    active = sum(min((times[i + 1] - times[i]).total_seconds(), GAP_CAP_SEC) for i in range(len(times) - 1))
    wall = (times[-1] - times[0]).total_seconds()
    return {"active_sec": int(active), "wall_sec": int(wall)}


def _git(repo, args, timeout=8):
    try:
        r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def git_stats(repo, since_iso, until_iso, author_email=None):
    """files/insertions/deletions/commits + commit subjects for commits in the
    [since, until] window (optionally by a single author). Empty if not a repo."""
    if not repo or not os.path.isdir(os.path.join(repo, ".git")):
        return {}
    args = ["log", f"--since={since_iso}", f"--until={until_iso}", "--numstat", "--pretty=format:%x01%H%x02%s"]
    if author_email:
        args.insert(1, f"--author={author_email}")
    out = _git(repo, args)
    if not out:
        return {"commits": 0, "files_changed": 0, "insertions": 0, "deletions": 0, "commit_messages": []}
    commits, files, ins, dele, subjects = 0, 0, 0, 0, []
    for line in out.splitlines():
        if line.startswith("\x01"):
            commits += 1
            parts = line[1:].split("\x02", 1)
            if len(parts) == 2 and parts[1].strip():
                subjects.append(parts[1].strip())
            continue
        cols = line.split("\t")
        if len(cols) == 3 and cols[0].isdigit() and cols[1].isdigit():
            files += 1
            ins += int(cols[0])
            dele += int(cols[1])
    return {"commits": commits, "files_changed": files, "insertions": ins,
            "deletions": dele, "commit_messages": subjects[:50]}


def _self_test():
    assert active_time([]) == {"active_sec": 0, "wall_sec": 0}
    m = active_time(["2026-07-21T10:00:00Z", "2026-07-21T10:04:00Z", "2026-07-21T11:00:00Z"])
    # gap1 = 240s (<=cap), gap2 = 3600s (capped to 300) -> 540 active; wall = 3600
    assert m == {"active_sec": 540, "wall_sec": 3600}, m
    assert git_stats("/nonexistent-repo-xyz", "2026-01-01", "2026-12-31") == {}
    print("OK self-test: metrics (active-time cap / non-repo)")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        _now = datetime.now(timezone.utc).isoformat()
        print(git_stats(sys.argv[1] if len(sys.argv) > 1 else ".", "1970-01-01", _now))
