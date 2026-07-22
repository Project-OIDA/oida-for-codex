#!/usr/bin/env python3
"""OIDA for Codex — session enumeration + ledger + skip queue.

The Codex-specific sibling of oida-for-claude/oida/engine/extract.py. The ledger,
skip queue, and git helpers (owner_repo_from_remote / git_info / git_email) are
SHARED LOGIC — keep them in sync with the Claude client. Only `session_files`
(the glob) and `describe_session` (which reads Codex's `session_meta` header
instead of Claude's per-line `cwd`/`timestamp`) are Codex-specific.

Discovers Codex rollout files, resolves each session's git repo (session-time
`git.repository_url` -> owner/repo, else live git in cwd) and time window, and
maintains an idempotency ledger (session_id + content-hash) and a skip queue
(sessions that fail schema detection, so a poison file is not retried forever).
No LLM, no server calls here.
"""
import glob
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sources  # noqa: E402

# uuid7 as written into the rollout filename: rollout-<iso>-<uuid>.jsonl
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _git(cwd, args, timeout=8):
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


_REMOTE_RE = re.compile(r"(?:git@[^:]+:|ssh://[^/]+/|https?://[^/]+/)([^/\s]+/[^/\s]+?)(?:\.git)?/?$")


def owner_repo_from_remote(remote):
    if not remote:
        return None
    m = _REMOTE_RE.search(remote.strip())
    return m.group(1) if m else None


def git_info(cwd):
    """{remote, owner_repo, branch} for the session's working dir, or {}."""
    if not cwd or not os.path.isdir(cwd):
        return {}
    remote = _git(cwd, ["config", "--get", "remote.origin.url"])
    branch = _git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"])
    info = {}
    if remote:
        info["remote"] = remote
    owner_repo = owner_repo_from_remote(remote)
    if owner_repo:
        info["owner_repo"] = owner_repo
    if branch and branch != "HEAD":
        info["branch"] = branch
    return info


def repo_from_meta(git_meta, cwd):
    """Prefer the repo recorded in session_meta.git (session-time truth); fall back
    to live git in the cwd. Returns {remote?, owner_repo?, branch?} or {}.

    A rollout file is local and could be crafted to claim an allowlisted
    `repository_url` while `cwd` points at a different repo, mis-attributing that
    repo's git_stats/email to the allowlisted name. So when the recorded repo
    disagrees with the live cwd remote, trust the cwd — it is what git_stats() and
    git_email() actually measure. (The server also re-enforces the allowlist, P6.)
    When there is no live cwd git to compare against, the recorded value is used
    as-is — the intended case where the working dir moved after the session."""
    live = git_info(cwd)
    if isinstance(git_meta, dict):
        url = git_meta.get("repository_url")
        owner_repo = owner_repo_from_remote(url)
        if owner_repo:
            live_owner_repo = live.get("owner_repo")
            if live_owner_repo and live_owner_repo != owner_repo:
                return live  # conflict: filesystem truth wins over the file's claim
            info = {"owner_repo": owner_repo}
            if url:
                info["remote"] = url
            if isinstance(git_meta.get("branch"), str) and git_meta["branch"]:
                info["branch"] = git_meta["branch"]
            return info
    return live


def git_email(cwd):
    return _git(cwd or ".", ["config", "--get", "user.email"]) or _git(".", ["config", "--get", "user.email"])


def _head_tail(path):
    first = last = None
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                if not isinstance(obj, dict):
                    continue
                if first is None:
                    first = obj
                last = obj
    except OSError:
        return None, None
    return first, last


def uuid_from_name(path):
    """The session uuid embedded in a rollout filename — the skip-queue key when
    describe_session can't read the header. Public: the planner uses it too."""
    m = _UUID_RE.search(os.path.basename(path))
    return m.group(0) if m else os.path.splitext(os.path.basename(path))[0]


def session_files(codex_roots):
    for root in codex_roots:
        for p in glob.glob(os.path.join(root, "**", "rollout-*.jsonl"), recursive=True):
            yield p


def describe_session(path):
    """Metadata for one Codex rollout, or None if its `session_meta` header is
    absent/unreadable (not a Codex rollout / drifted schema -> caller skip-queues).
    The stable session identity is `session_meta.payload.id` (falls back to the
    uuid in the filename)."""
    first, last = _head_tail(path)
    if not first or first.get("type") != "session_meta":
        return None
    payload = first.get("payload")
    if not isinstance(payload, dict):
        return None
    cwd = payload.get("cwd") if isinstance(payload.get("cwd"), str) else None
    session_id = payload.get("id") if isinstance(payload.get("id"), str) and payload.get("id") else uuid_from_name(path)
    started = payload.get("timestamp") if isinstance(payload.get("timestamp"), str) else \
        (first.get("timestamp") if isinstance(first.get("timestamp"), str) else None)
    ended = last.get("timestamp") if isinstance(last.get("timestamp"), str) else started
    return {
        "session_id": session_id,
        "path": path,
        "cwd": cwd,
        "repo": repo_from_meta(payload.get("git"), cwd),
        "started_at": started,
        "ended_at": ended,
        "mtime": os.path.getmtime(path),
    }


# -- idempotency ledger + skip queue (atomic JSON files under the work dir) -------
# SHARED LOGIC — identical to the Claude client.
def _load_json(p, default):
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save_json(p, data):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, p)


def content_key(session_id, content_hash):
    return f"{session_id}:{content_hash}"


def load_ledger(work):
    return set(_load_json(os.path.join(work, "ledger.json"), []))


def record_ledger(work, key):
    seen = load_ledger(work)
    seen.add(key)
    _save_json(os.path.join(work, "ledger.json"), sorted(seen))


def load_skip(work):
    return set(_load_json(os.path.join(work, "skip.json"), []))


def record_skip(work, session_id):
    skip = load_skip(work)
    skip.add(session_id)
    _save_json(os.path.join(work, "skip.json"), sorted(skip))


def _self_test():
    assert owner_repo_from_remote("git@github.com:kakashi-ventures/oida.git") == "kakashi-ventures/oida"
    assert owner_repo_from_remote("https://github.com/kakashi-ventures/oida") == "kakashi-ventures/oida"
    assert owner_repo_from_remote("https://github.com/kakashi-ventures/oida.git") == "kakashi-ventures/oida"
    assert owner_repo_from_remote("") is None and owner_repo_from_remote(None) is None
    assert content_key("s1", "abc") == "s1:abc"
    # session-time git wins over live cwd git; branch carried through
    r = repo_from_meta({"repository_url": "git@github.com:kva/oida.git", "branch": "main"}, "/nope")
    assert r == {"owner_repo": "kva/oida", "remote": "git@github.com:kva/oida.git", "branch": "main"}, r
    # a recorded repo that disagrees with the live cwd remote must NOT win (5B):
    # the filesystem truth (git_info) does. Monkeypatch the module-global git_info.
    _orig_git_info = globals()["git_info"]
    try:
        globals()["git_info"] = lambda _cwd: {"owner_repo": "real/local", "remote": "git@github.com:real/local.git"}
        conflict = repo_from_meta({"repository_url": "git@github.com:evil/allowlisted.git"}, "/whatever")
        assert conflict == {"owner_repo": "real/local", "remote": "git@github.com:real/local.git"}, conflict
    finally:
        globals()["git_info"] = _orig_git_info
    assert uuid_from_name("rollout-2026-06-23T16-40-09-019ef4ec-a261-7c62-825a-fb058d3d38bc.jsonl") \
        == "019ef4ec-a261-7c62-825a-fb058d3d38bc"
    print("OK self-test: extract (remote parsing / content key / meta-repo / uuid)")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        print(json.dumps({"codex_roots": sources.codex_session_roots()}, indent=2))
