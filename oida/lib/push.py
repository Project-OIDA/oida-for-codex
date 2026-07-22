#!/usr/bin/env python3
"""OIDA for Codex — build + push SessionEnvelopes (deterministic, no LLM).

The Codex-specific sibling of oida-for-claude/oida/lib/push.py. Identical wire
behaviour — the ONLY differences are SURFACE ("codex_cli") and CLIENT_VERSION.
The server validates the same SessionEnvelope for both surfaces (SESSION_SURFACES
already admits 'codex_cli').

For each planned session: assemble the SessionEnvelope, gzip it, POST to
/ingest/sessions with the device bearer, retry with backoff on 5xx/network, and
record the ledger key on a 2xx. The server is idempotent (recordDelivery on
session_id + content-hash), so at-least-once is safe: a retry that already landed
is deduped, never double-ingested. A session that fails to parse (poison / drifted
schema) is skip-queued, never allowed to wedge the rest.
"""
import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "engine"))
import extract  # noqa: E402
import metrics  # noqa: E402
import transcript  # noqa: E402

CLIENT_VERSION = "oida-codex-client/0.1.0"
SURFACE = "codex_cli"
MAX_ATTEMPTS = 4


def load_config():
    path = os.environ.get("OIDA_CONFIG") or os.path.join(os.path.expanduser("~"), ".oida", "config.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def build_envelope(desc):
    """Assemble the SessionEnvelope from a plan descriptor. Every string in the
    transcript was already redacted by transcript.py. Raises on an unparseable
    rollout (caller skip-queues)."""
    t = transcript.parse_transcript(desc["path"])
    started = desc.get("started_at") or _now_iso()
    ended = desc.get("ended_at") or _now_iso()
    cwd = desc.get("cwd")
    email = extract.git_email(cwd)
    envelope = {
        "session_id": desc["session_id"],
        "surface": SURFACE,
        "repo": desc["repo"],  # owner_repo guaranteed by the planner; remote/branch optional
        "started_at": started,
        "ended_at": ended,
        "update": bool(desc.get("update", False)),
        "transcript": {
            "turns": t["turns"],
            "tool_calls": t["tool_calls"],
            "metrics": {
                **metrics.active_time([x.get("at") for x in t["turns"] if x.get("at")]),
                "turns": len(t["turns"]),
                "tool_calls": len(t["tool_calls"]),
            },
            "git_stats": metrics.git_stats(cwd, started, ended, email) or {},
        },
        "client_version": CLIENT_VERSION,
    }
    if email:
        envelope["git_email"] = email
    return envelope


def push_one(config, envelope):
    """POST one envelope. Retries 5xx/network up to MAX_ATTEMPTS with exponential
    backoff. Returns a status the caller acts on:
      "ok"        — 2xx: record the ledger, done.
      "skip"      — permanent content rejection (400 bad envelope / 413 too large):
                    skip-queue it so it is NOT re-planned and re-POSTed every scan.
      "auth"      — 401/403: the device key is bad/revoked; EVERY push will fail the
                    same way, so the caller aborts the run (nothing skip-queued, so
                    sessions resume after the key is fixed).
      "transient" — 5xx/network after MAX_ATTEMPTS: leave unrecorded, retry next scan.
    """
    url = config["apiUrl"].rstrip("/") + "/ingest/sessions"
    body = gzip.compress(json.dumps(envelope).encode("utf-8"))
    last = "unknown"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": "Bearer " + config["deviceKey"],
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return "ok" if 200 <= r.status < 300 else "transient"
        except urllib.error.HTTPError as e:
            if e.code in (400, 413):
                print(f"{_now_iso()}: push {envelope['session_id']} rejected ({e.code}) — skip-queued (won't retry)")
                return "skip"  # permanent for this content: bad envelope / too large for plan
            if e.code in (401, 403):
                print(f"{_now_iso()}: push {envelope['session_id']} auth rejected ({e.code}) — aborting run; re-install the device key")
                return "auth"  # bad/revoked key: aborts the whole run, nothing skip-queued
            last = f"HTTP {e.code}"
        except Exception as e:  # noqa: BLE001 — network/timeout are all transient here
            last = str(e)
        if attempt < MAX_ATTEMPTS:
            time.sleep(min(2 ** attempt, 30))
    print(f"{_now_iso()}: push {envelope['session_id']} failed after {MAX_ATTEMPTS} attempts: {last}")
    return "transient"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", required=True)
    ap.add_argument("--work", required=True)
    args = ap.parse_args(argv)
    config = load_config()
    with open(args.plan, encoding="utf-8") as f:
        plan = json.load(f)

    pushed = 0
    for desc in plan:
        try:
            envelope = build_envelope(desc)
        except Exception as e:  # noqa: BLE001 — a poison session must not wedge the rest
            print(f"{_now_iso()}: build failed for {desc.get('session_id')}: {e} — skip-queued")
            extract.record_skip(args.work, desc.get("session_id", "?"))
            continue
        status = push_one(config, envelope)
        if status == "ok":
            extract.record_ledger(args.work, desc["ledger_key"])
            pushed += 1
        elif status == "skip":
            extract.record_skip(args.work, desc.get("session_id", "?"))
        elif status == "auth":
            print(f"{_now_iso()}: aborting run — device key rejected; fix it and re-run")
            break
        # "transient": leave unrecorded so the next scan retries it
    print(f"{_now_iso()}: pushed {pushed}/{len(plan)} session(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
