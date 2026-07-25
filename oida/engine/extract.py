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
from redact import redact  # noqa: E402

# uuid7 as written into the rollout filename: rollout-<iso>-<uuid>.jsonl
_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _git(cwd, args, timeout=8):
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


_REMOTE_RE = re.compile(
    r"(?:git@(?P<h1>[^:]+):|ssh://(?:[^@/]+@)?(?P<h2>[^/:]+)(?::\d+)?/|https?://(?:[^@/]+@)?(?P<h3>[^/:]+)(?::\d+)?/)"
    r"(?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?/?$")


def _remote_match(remote):
    return _REMOTE_RE.search(remote.strip()) if remote else None


def owner_repo_from_remote(remote):
    m = _remote_match(remote)
    return m.group("repo") if m else None


def host_from_remote(remote):
    """The remote's host, lowercased — `owner/repo` alone is not an identity.
    Two different hosts can serve the same owner/repo, so the allowlist check
    would otherwise fail OPEN for a same-named repo on another host."""
    m = _remote_match(remote)
    if not m:
        return None
    host = m.group("h1") or m.group("h2") or m.group("h3")
    return host.lower() if host else None


def git_info(cwd):
    """{remote, host, owner_repo, branch} for the session's working dir, or {}.

    `remote` is REDACTED: credentialed remotes are common
    (https://oauth2:ghp_…@github.com/acme/app.git) and this value goes into the
    envelope verbatim, so the token would ship with it."""
    if not cwd or not os.path.isdir(cwd):
        return {}
    remote = _git(cwd, ["config", "--get", "remote.origin.url"])
    branch = _git(cwd, ["rev-parse", "--abbrev-ref", "HEAD"])
    info = {}
    if remote:
        info["remote"] = redact(remote)
    host = host_from_remote(remote)
    if host:
        info["host"] = host
    owner_repo = owner_repo_from_remote(remote)
    if owner_repo:
        info["owner_repo"] = owner_repo
    if branch and branch != "HEAD":
        info["branch"] = branch
    return info


# Hosts whose `owner/repo` may be matched against a host-less allowlist entry.
# The designated scopes are GitHub repos, so github.com is the only default;
# GitHub Enterprise deployments add theirs via config.json `gitHosts`.
DEFAULT_GIT_HOSTS = ("github.com",)


def repo_allowed(repo_info, allow, hosts=DEFAULT_GIT_HOSTS):
    """Client-side allowlist gate (the server re-enforces P6 regardless).

    `owner/repo` alone is not a repo identity: a session in gitlab.com/acme/app
    or a personal fork on another host would match a workspace that designated
    acme/app on GitHub — a default-deny gate failing OPEN. So an entry matches
    only if it names the host explicitly (`host/owner/repo`), or if it is
    host-less and the session's host is one we accept for host-less entries."""
    owner_repo = (repo_info or {}).get("owner_repo")
    if not owner_repo:
        return False
    host = (repo_info or {}).get("host")
    allow = {str(a).strip().lower().lstrip("/") for a in (allow or set())}
    if host and f"{host}/{owner_repo}".lower() in allow:
        return True
    if owner_repo.lower() not in allow:
        return False
    # Host-less entry: accept only from a host we treat as the designated one.
    # A session with no resolvable host (no remote) never passes.
    return bool(host) and host in {h.lower() for h in hosts}


def repo_from_meta(git_meta, cwd):
    """Prefer the repo recorded in session_meta.git (session-time truth); fall back
    to live git in the cwd. Returns {remote?, host?, owner_repo?, branch?} or {}.

    A rollout file is local and could be crafted to claim an allowlisted
    `repository_url` while `cwd` points somewhere else, mis-attributing content
    to the allowlisted name. Two guards, both preferring filesystem truth:
      - the recorded repo DISAGREES with the live cwd remote -> trust the cwd; it
        is what git_stats() and git_email() actually measure;
      - the cwd EXISTS but is not a git repo -> the record is uncorroborated with
        no legitimate explanation (a real session's cwd is a repo), so it is
        refused; the session then has no owner_repo and the planner drops it.
    The record is used as-is only when the cwd is GONE — the intended case where
    the working directory was moved or deleted after the session. (The server
    re-enforces the allowlist regardless, P6.)"""
    live = git_info(cwd)
    if isinstance(git_meta, dict):
        url = git_meta.get("repository_url")
        owner_repo = owner_repo_from_remote(url)
        if owner_repo:
            live_owner_repo = live.get("owner_repo")
            if live_owner_repo and live_owner_repo != owner_repo:
                return live  # conflict: filesystem truth wins over the file's claim
            if not live_owner_repo and cwd and os.path.isdir(cwd):
                return live  # cwd is there and is not that repo -> claim refused
            info = {"owner_repo": owner_repo}
            if url:
                info["remote"] = redact(url)
            host = host_from_remote(url)
            if host:
                info["host"] = host
            if isinstance(git_meta.get("branch"), str) and git_meta["branch"]:
                info["branch"] = git_meta["branch"]
            return info
    return live


def git_email(cwd):
    """The session repo's configured author email.

    No fallback to the process CWD: push.py runs detached with an inherited
    working directory, so `.` is often an unrelated repo — that email would be
    stored as the envelope's author AND passed as git_stats' --author filter,
    silently attributing (or zeroing) another repo's work."""
    return _git(cwd, ["config", "--get", "user.email"]) if cwd else ""


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
    # The host is part of the repo identity (allowlist must not fail open).
    assert host_from_remote("git@github.com:kva/oida.git") == "github.com"
    assert host_from_remote("https://GitLab.com/kva/oida.git") == "gitlab.com"
    assert host_from_remote("ssh://git@git.internal:2222/kva/oida.git") == "git.internal"
    assert host_from_remote("https://oauth2:ghp_x@github.com/kva/oida.git") == "github.com"
    assert owner_repo_from_remote("https://oauth2:ghp_x@github.com/kva/oida.git") == "kva/oida"
    assert host_from_remote("not-a-remote") is None
    # Host-blind allowlist matching must not fail open.
    allow = {"kva/oida", "github.com/kva/other"}
    assert repo_allowed({"owner_repo": "kva/oida", "host": "github.com"}, allow)
    assert not repo_allowed({"owner_repo": "kva/oida", "host": "gitlab.com"}, allow)
    assert not repo_allowed({"owner_repo": "kva/oida"}, allow)  # no host → no match
    assert repo_allowed({"owner_repo": "kva/oida", "host": "git.acme.dev"}, allow, hosts=("git.acme.dev",))
    assert repo_allowed({"owner_repo": "kva/other", "host": "github.com"}, allow)
    assert not repo_allowed({"owner_repo": "kva/other", "host": "gitlab.com"}, allow)
    assert not repo_allowed({}, allow) and not repo_allowed(None, allow)
    assert not repo_allowed({"owner_repo": "kva/oida", "host": "github.com"}, set())
    assert content_key("s1", "abc") == "s1:abc"
    # session-time git wins when the cwd is GONE; branch + host carried through
    r = repo_from_meta({"repository_url": "git@github.com:kva/oida.git", "branch": "main"}, "/nope")
    assert r == {"owner_repo": "kva/oida", "remote": "git@github.com:kva/oida.git",
                 "host": "github.com", "branch": "main"}, r
    # a recorded repo that disagrees with the live cwd remote must NOT win (5B):
    # the filesystem truth (git_info) does. Monkeypatch the module-global git_info.
    _orig_git_info = globals()["git_info"]
    try:
        globals()["git_info"] = lambda _cwd: {"owner_repo": "real/local", "remote": "git@github.com:real/local.git"}
        conflict = repo_from_meta({"repository_url": "git@github.com:evil/allowlisted.git"}, "/whatever")
        assert conflict == {"owner_repo": "real/local", "remote": "git@github.com:real/local.git"}, conflict
        # …and an EXISTING cwd that is not a git repo refuses the claim outright:
        # a crafted rollout must not attribute itself to an allowlisted repo.
        globals()["git_info"] = lambda _cwd: {}
        crafted = repo_from_meta({"repository_url": "git@github.com:evil/allowlisted.git"},
                                 os.path.dirname(os.path.abspath(__file__)))
        assert crafted == {}, crafted
    finally:
        globals()["git_info"] = _orig_git_info
    assert uuid_from_name("rollout-2026-06-23T16-40-09-019ef4ec-a261-7c62-825a-fb058d3d38bc.jsonl") \
        == "019ef4ec-a261-7c62-825a-fb058d3d38bc"
    print("OK self-test: extract (remote parsing / host / allowlist / meta-repo / uuid)")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        print(json.dumps({"codex_roots": sources.codex_session_roots()}, indent=2))
