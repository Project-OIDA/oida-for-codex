#!/usr/bin/env python3
"""OIDA for Codex — cross-OS/WSL discovery of local Codex CLI data sources.

The Codex-specific sibling of oida-for-claude/oida/engine/sources.py. Same
WSL/Windows-user resolution (single source of truth for where a CLI writes its
transcripts); only the path differs.

Codex CLI rollout transcripts live at `<home>/.codex/sessions/YYYY/MM/DD/
rollout-<iso>-<uuid>.jsonl` on all platforms, plus the Windows-side root under
`/mnt/c/Users/<W>` when running in WSL2. Pure assembly functions (testable with
injected inputs) + thin public wrappers that wire the real environment. No
third-party deps. `--self-test` checks the pure branches.
"""
import argparse, os, sys, subprocess

SYSTEM_PROFILES = {"Default", "Default User", "Public", "All Users", "desktop.ini",
                   "Administrator", "WDAGUtilityAccount"}


def detect_wsl():
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    for p in ("/proc/sys/kernel/osrelease", "/proc/version"):
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                if "microsoft" in f.read().lower():
                    return True
        except OSError:
            pass
    return False


def windows_user_profiles(run=subprocess.run, listdir=os.listdir, isdir=os.path.isdir):
    """The CURRENT Windows user's `/mnt/c/Users/<W>` (privacy: never read several users' data)."""
    for cmd in (["powershell.exe", "-NoProfile", "-Command", "$env:USERPROFILE"],
                ["cmd.exe", "/c", "echo %USERPROFILE%"]):
        try:
            r = run(cmd, capture_output=True, text=True, timeout=8, cwd="/mnt/c")
            lines = [line.strip() for line in (r.stdout or "").splitlines() if line.strip()]
            prof = lines[-1] if lines else ""
            if prof[:9].lower() == "c:\\users\\":
                user = prof.split("\\")[2]
                if user:
                    return ["/mnt/c/Users/" + user]
        except Exception:
            pass
    base = "/mnt/c/Users"
    if not isdir(base):
        return []
    try:
        names = [n for n in listdir(base) if n not in SYSTEM_PROFILES and isdir(os.path.join(base, n))]
    except OSError:
        return []
    me = (os.environ.get("USER") or "").lower()
    match = [n for n in names if n.lower() == me]
    if match:
        return [os.path.join(base, match[0])]

    def _has_codex(n):
        return isdir(os.path.join(base, n, ".codex", "sessions"))
    with_data = [n for n in names if _has_codex(n)]
    if len(with_data) == 1:
        return [os.path.join(base, with_data[0])]
    if len(names) == 1:
        return [os.path.join(base, names[0])]
    return []  # genuinely ambiguous -> skip the Windows side


def assemble_codex_roots(home, is_wsl, win_profiles):
    roots = [os.path.join(home, ".codex", "sessions")]
    if is_wsl:
        roots += [os.path.join(w, ".codex", "sessions") for w in win_profiles]
    return roots


def _existing(paths, exists=os.path.exists):
    return [p for p in paths if exists(p)]


def codex_session_roots():
    is_wsl = detect_wsl()
    wp = windows_user_profiles() if is_wsl else []
    return _existing(assemble_codex_roots(os.path.expanduser("~"), is_wsl, wp))


def describe():
    return {"is_wsl": detect_wsl(), "platform": sys.platform, "codex_roots": codex_session_roots()}


def _self_test():
    assert assemble_codex_roots("/home/bob", False, []) == ["/home/bob/.codex/sessions"]
    wsl = assemble_codex_roots("/home/bob", True, ["/mnt/c/Users/bob"])
    assert wsl == ["/home/bob/.codex/sessions", "/mnt/c/Users/bob/.codex/sessions"], wsl

    class _R:  # noqa
        stdout = "C:\\Users\\alice\r\n"
    assert windows_user_profiles(run=lambda *a, **k: _R()) == ["/mnt/c/Users/alice"]

    def _boom(*a, **k):
        raise OSError("no powershell")
    _prev_user = os.environ.get("USER")
    os.environ["USER"] = "__nomatch__"
    try:
        assert windows_user_profiles(run=_boom, listdir=lambda b: ["Default", "carol"],
                                     isdir=lambda p: True) == ["/mnt/c/Users/carol"]
        assert windows_user_profiles(run=_boom, listdir=lambda b: ["alice", "bob"],
                                     isdir=lambda p: True) == []
    finally:
        if _prev_user is None:
            os.environ.pop("USER", None)
        else:
            os.environ["USER"] = _prev_user
    print("OK self-test: sources (codex roots / win-user resolution)")


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv)
    if args.self_test:
        _self_test()
        return 0
    import json
    print(json.dumps(describe(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
