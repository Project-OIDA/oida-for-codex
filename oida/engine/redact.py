#!/usr/bin/env python3
"""OIDA for Codex — deterministic local redaction.

⚠️  SHARED CODE — the executable body below (patterns + redact() + self-test) is kept
    byte-identical with oida-for-claude/oida/engine/redact.py; only this docstring differs
    intentionally. This is surface-agnostic (secret shapes, not transcript shapes). If you
    change a pattern here, change it there too (verify: `diff` the files from the first
    `import` line on — that region must be empty). Duplicated rather than submoduled so each
    client repo stays self-contained and installable without --recurse-submodules.

Every string that leaves this machine (turn text, tool_call name + input) is passed
through redact() before it is put in the SessionEnvelope. The `oida_sess_…` pattern
scrubs OIDA's own device token in case a transcript echoed the key at install time;
the openai/anthropic patterns matter especially for Codex sessions. `--self-test`
checks each pattern fires.

This is a best-effort secret scrub, not a guarantee: the server treats the
transcript as already-clean and does no further redaction, so keep this list in
sync with new credential shapes.
"""
import re
import sys

_REDACTIONS = [
    (re.compile(r"oida_sess_[A-Za-z0-9]{6,}_[A-Za-z0-9]{6,}"), "«redacted:oida_device_key»"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{12,}"), "«redacted:anthropic_key»"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "«redacted:openai_key»"),
    (re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}"), "«redacted:stripe_key»"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "«redacted:google_api_key»"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{20,}"), "«redacted:npm_token»"),
    (re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:@/]+:[^\s@/]+@\S+"), "«redacted:connection_string»"),
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]+"), "«redacted:slack_webhook»"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}"), "«redacted:jwt»"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"), "«redacted:github_token»"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "«redacted:aws_key»"),
    (re.compile(r"\bkva_[A-Za-z0-9]{12,}"), "«redacted:kva_key»"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}"), "«redacted:slack_token»"),
    (re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"), "«redacted:private_key»"),
    (re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"), "«redacted:private_key»"),
    (re.compile(r"(?i)\b(?:authorization|bearer)\b[:\s]+[A-Za-z0-9._\-]{16,}"), "«redacted:bearer»"),
    (re.compile(
        r"(?i)\b([A-Z0-9_]*(?:API_?KEY|SECRET|TOKEN|PASSWORD|PASSWD|PRIVATE_KEY))\b\s*[:=]\s*['\"]?[^\s'\"]{8,}"),
        r"\1=«redacted»"),
]


def redact(s):
    """Redact secrets from a string. None/empty pass through unchanged."""
    if not s:
        return s
    for rx, repl in _REDACTIONS:
        s = rx.sub(repl, s)
    return s


def _self_test():
    cases = [
        ("oida_sess_deadbeefcafe0000_0123456789abcdef0123456789abcdef", "oida_device_key"),
        ("key sk-ant-api03-abcdefghijklmnop", "anthropic_key"),
        ("postgres://user:pass@db.example.com:5432/x", "connection_string"),
        ("token ghp_0123456789abcdefghijklmnopqrstuvwxyz", "github_token"),
        ("AKIAIOSFODNN7EXAMPLE here", "aws_key"),
        ("Authorization: Bearer abcdef0123456789abcdef", "bearer"),
        ('DATABASE_PASSWORD="hunter2secret"', "redacted"),
        ("stripe_key=sk_live_0123456789abcdefghij", "stripe_key"),
        ("rk_test_0123456789abcdefghij here", "stripe_key"),
        ("google AIzaSyA1B2C3D4E5F6G7H8I9J0KLMNOPqrstuvw", "google_api_key"),
        ("token npm_0123456789abcdefghij0123456789abcd", "npm_token"),
        ("hook https://hooks.slack.com/services/T000/B000/xxxxxxxxxxxxxxxxxxxxxxxx", "slack_webhook"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----", "private_key"),
    ]
    for raw, marker in cases:
        out = redact(raw)
        assert marker in out, f"expected {marker!r} in redaction of {raw!r}, got {out!r}"
    assert redact("just a normal sentence about a decision") == "just a normal sentence about a decision"
    assert redact(None) is None and redact("") == ""
    print("OK self-test: redact (%d patterns)" % len(_REDACTIONS))


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        sys.stdout.write(redact(sys.stdin.read()))
