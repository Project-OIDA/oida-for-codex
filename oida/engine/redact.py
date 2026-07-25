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

# --- credential-bearing identifier names -------------------------------------
# The keyword may sit ANYWHERE in the name, not only at the end: the canonical
# offenders (SECRET_KEY, AWS_SECRET_ACCESS_KEY, SECRET_KEY_BASE, ENCRYPTION_KEY)
# all put a keyword mid-name, and their values have no distinctive shape — so if
# the name doesn't match, they leave the machine in full.
#
# Matching is component-wise (`_`, `.` or `-` separated): a keyword must BE a
# component, so MONKEY=, KEYWORDS= and TOKENIZER= are not treated as
# credentials. Applied case-insensitively.
_SECRET_KEYWORD = r"(?:API_?KEY|ACCESS_?KEY|PRIVATE_?KEY|SECRET|TOKEN|PASSWORD|PASSWD|PASSPHRASE|CREDENTIALS?|KEY)"
_SECRET_NAME = r"(?:[A-Z0-9]+[_.\-])*" + _SECRET_KEYWORD + r"(?:[_.\-][A-Z0-9]+)*"

# camelCase has no separator to key on, so the component rule above misses
# `apiSecret`, `dbPassword`, `authToken`, `clientSecret` — the ordinary shape of
# a JS/TS config object, i.e. exactly what a coding transcript is full of. Here
# the capital letter IS the boundary, so this rule is matched CASE-SENSITIVELY
# (a lowercase `monkey` / `tokenizer` has no hump and cannot match).
_CAMEL_KEYWORD = r"(?:Api)?(?:Key|Secret|Token|Password|Passwd|Passphrase|Credentials?)"
_CAMEL_NAME = r"[a-z][a-zA-Z0-9]*" + _CAMEL_KEYWORD + r"[a-zA-Z0-9]*"

# Name → value separator, tolerating the name's own closing quote (JSON/YAML
# quoted keys) between the identifier and the `:`/`=`.
_SEP = r"['\"]?\s*[:=]\s*"

_REDACTIONS = [
    # Device key: match the whole opaque tail whatever its internal structure
    # (single segment, base64url with -/_, …) — this is OIDA's own credential.
    (re.compile(r"oida_sess_[A-Za-z0-9_\-]{12,}"), "«redacted:oida_device_key»"),
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{12,}"), "«redacted:anthropic_key»"),
    (re.compile(r"sk-[A-Za-z0-9_\-]{20,}"), "«redacted:openai_key»"),
    (re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}"), "«redacted:stripe_key»"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "«redacted:google_api_key»"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{20,}"), "«redacted:npm_token»"),
    (re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"), "«redacted:sendgrid_key»"),
    (re.compile(r"\b[a-z][a-z0-9+.\-]*://[^\s:@/]+:[^\s@/]+@\S+"), "«redacted:connection_string»"),
    (re.compile(r"https://hooks\.slack\.com/services/[A-Za-z0-9/_\-]+"), "«redacted:slack_webhook»"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}"), "«redacted:jwt»"),
    (re.compile(r"\b(?:ghp|gho|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}"), "«redacted:github_token»"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "«redacted:aws_key»"),
    (re.compile(r"\bkva_[A-Za-z0-9]{12,}"), "«redacted:kva_key»"),
    # Every xox* family (bot/user/app-config/export/legacy) + app-level tokens.
    (re.compile(r"\bxox[a-z]-[A-Za-z0-9-]{10,}"), "«redacted:slack_token»"),
    (re.compile(r"\bxapp-[A-Za-z0-9-]{10,}"), "«redacted:slack_app_token»"),
    (re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----[\s\S]*?-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"), "«redacted:private_key»"),
    (re.compile(r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"), "«redacted:private_key»"),
    # Bearer values are base64 as often as base64url — +, / and = must be eaten
    # too, or everything after the first + survives. The auth scheme (Bearer /
    # Basic / token) may sit between the header name and the value.
    (re.compile(
        r"(?i)\b(?:authorization\b[:\s]+(?:(?:bearer|basic|token)[:\s]+)?|bearer\b[:\s]+)"
        r"[A-Za-z0-9._\-+/=]{16,}"),
        "«redacted:bearer»"),
    # NAME = "value with spaces": the quoted form is redacted up to the closing
    # quote, so a passphrase is not left in the clear by its first space.
    #
    # `_SEP` allows the name's own closing quote before the separator, so a
    # JSON/YAML quoted key matches: `{"DB_PASSWORD": "…"}` is how a secret most
    # often appears in a transcript (a tool result, a file read, a request body),
    # and without it the name ran into `"` instead of `:` and nothing matched.
    (re.compile(r"(?i)\b(" + _SECRET_NAME + r")" + _SEP + r"(['\"])[^'\"\n]{4,}?\2"), r"\1=«redacted»"),
    (re.compile(r"(?i)\b(" + _SECRET_NAME + r")" + _SEP + r"['\"]?[^\s'\"]{8,}"), r"\1=«redacted»"),
    # Same two rules for camelCase names — case-sensitive, no (?i).
    (re.compile(r"\b(" + _CAMEL_NAME + r")" + _SEP + r"(['\"])[^'\"\n]{4,}?\2"), r"\1=«redacted»"),
    (re.compile(r"\b(" + _CAMEL_NAME + r")" + _SEP + r"['\"]?[^\s'\"]{8,}"), r"\1=«redacted»"),
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
        # Single-segment / base64url device keys must scrub too (the old pattern
        # assumed exactly two alphanumeric runs joined by one underscore).
        ("oida_sess_9fA-bQ_x1Zc3TgH7pL", "oida_device_key"),
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
        # The *_KEY family: the keyword is mid-name and the value has no shape,
        # so a name miss meant the secret shipped verbatim.
        ('SECRET_KEY = "django-insecure-9v!x_q2m4z8w1e5r7t3y6u0i"', "redacted"),
        ('AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"', "redacted"),
        ("SECRET_KEY_BASE=0123456789abcdef0123456789abcdef", "redacted"),
        ("ENCRYPTION_KEY=0123456789abcdef", "redacted"),
        ("apiKey: abcdefghijklmnop", "redacted"),
        ("my-access-key=abcdefghijklmnop", "redacted"),
        # camelCase: no separator, so the component rule can't see the keyword.
        ('{"apiSecret": "9f8e7d6c5b4a39281706"}', "redacted"),
        ("const dbPassword = 'hunter2hunter2'", "redacted"),
        ("authToken: eyJhbGciOiJI0000000000", "redacted"),
        ("clientSecret=GOCSPX-abcdefghijklmnop", "redacted"),
        # Quoted JSON/YAML keys — the name's own quote sat between it and the ":".
        ('{"DB_PASSWORD": "hunter2hunter2"}', "redacted"),
        ('{"api_secret": "9f8e7d6c5b4a39281706"}', "redacted"),
        ('  "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG"', "redacted"),
        # A quoted value with spaces used to survive from its first space on.
        ('DB_PASSWORD="correct horse battery staple"', "redacted"),
        # Slack: app-level and the xox families beyond xox[baprs].
        ("xapp-1-A0123ABC-4567890123456-abcdef0123456789", "slack_app_token"),
        ("xoxc-1234567890-abcdefghijklmnop", "slack_token"),
        # A standard-base64 bearer must not survive past its first + or /.
        ("Authorization: Basic YWJjOmRlZg+/12345678901234==", "bearer"),
        ("SENDGRID=SG.aBcDeFgHiJkLmNoP.qRsTuVwXyZ0123456789abcd", "sendgrid_key"),
    ]
    for raw, marker in cases:
        out = redact(raw)
        assert marker in out, f"expected {marker!r} in redaction of {raw!r}, got {out!r}"
    # No leftovers: these values must not appear anywhere in the output.
    leak_cases = [
        ('SECRET_KEY = "django-insecure-9v!x_q2m4z8w1e5r7t3y6u0i"', "django-insecure"),
        ('AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"', "wJalrXUtnFEMI"),
        ('DB_PASSWORD="correct horse battery staple"', "battery"),
        ("Authorization: Basic YWJjOmRlZg+/12345678901234==", "12345678901234"),
        ('{"apiSecret": "9f8e7d6c5b4a39281706"}', "9f8e7d6c5b4a"),
        ("const dbPassword = 'hunter2hunter2'", "hunter2"),
        ('{"DB_PASSWORD": "hunter2hunter2"}', "hunter2"),
        ('  "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG"', "wJalrXUtnFEMI"),
    ]
    for raw, secret in leak_cases:
        out = redact(raw)
        assert secret not in out, f"{secret!r} leaked through redaction of {raw!r}: {out!r}"
    # Names that merely CONTAIN a keyword's letters are not credentials. The
    # camelCase rule needs the capital hump, so all-lowercase names are safe too.
    for benign in ["MONKEY=banana bread", "KEYWORDS=alpha,beta,gamma", "TOKENIZER=wordpiece-v2",
                   "monkey=banana bread", "keywords=alpha,beta,gamma", "tokenizer=wordpiece-v2"]:
        assert redact(benign) == benign, f"false positive on {benign!r}: {redact(benign)!r}"
    assert redact("just a normal sentence about a decision") == "just a normal sentence about a decision"
    assert redact(None) is None and redact("") == ""
    print("OK self-test: redact (%d patterns)" % len(_REDACTIONS))


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        sys.stdout.write(redact(sys.stdin.read()))
