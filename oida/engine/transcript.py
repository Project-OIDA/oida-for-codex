#!/usr/bin/env python3
"""OIDA for Codex — Codex CLI rollout JSONL -> transcript {turns, tool_calls}.

The Codex-specific sibling of oida-for-claude's engine/transcript.py. The two
clients share the SAME wire contract (SessionEnvelope) and the same deliberate
reduction — every user/assistant turn verbatim (redacted) + a tool-call LOG
(tool name + redacted, truncated input); tool *output* bodies are dropped
(~90% of bytes, the biggest secret-leak surface). Only the parser differs,
because the on-disk format differs.

Codex rollout format (verified against ~/.codex/sessions, 2026-07-21; see
docs/SCHEMA.md). Every line is an envelope `{timestamp, type, payload}`:
  - type "session_meta"   — line 0: payload.{id, cwd, timestamp, git{...}}. Not a turn.
  - type "response_item"  — the Responses-API conversation. payload.type:
        "message"                -> role user/assistant/developer; content parts
                                    {type: input_text|output_text|input_image, text}
        "function_call"          -> {name, arguments}      -> tool_call
        "custom_tool_call"       -> {name, input}          -> tool_call
        "web_search_call"        -> {action.query}         -> tool_call (name web_search)
        "function_call_output"   -> tool result            -> DROPPED
        "custom_tool_call_output"-> tool result            -> DROPPED
        "reasoning"              -> encrypted model CoT     -> DROPPED
  - type "event_msg"      — TUI telemetry (user_message/agent_message duplicate the
                            response_item conversation) -> IGNORED (avoid double count).
  - type "turn_context"   — per-turn config -> IGNORED.

Tolerant: an unparseable/unknown line is skipped, never fatal. A file with content
but ZERO recognizable Codex envelopes raises UnknownRolloutSchema so the caller can
route it to the skip queue (plan's "unknown schema -> skip, never crash").
Every string is passed through redact() before it leaves this module.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from redact import redact  # noqa: E402

MAX_TOOL_INPUT = 2000  # a redacted tool input is provenance, not payload — truncate hard.

# The envelope `type` values we recognize. Seeing none of these across a non-empty
# file means it is not a Codex rollout (or the format drifted) -> skip queue.
_KNOWN_TYPES = {"session_meta", "response_item", "event_msg", "turn_context"}


class UnknownRolloutSchema(Exception):
    """Raised when a non-empty file contains no recognizable Codex rollout lines."""


def _content_text(content):
    """A response_item message `content` is a string or a list of typed parts;
    return the concatenated text of every part that carries a string `text`
    (input_text / output_text / …). Image parts have no `text` and are skipped.
    Liberal on the part `type` on purpose — robust to new text-part names."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = [p["text"] for p in content
               if isinstance(p, dict) and isinstance(p.get("text"), str)]
        return "\n".join(out)
    return ""


def _tool_call(payload, ts):
    """Map a function_call / custom_tool_call / web_search_call payload to a
    redacted, truncated tool-call log entry. Returns None for anything else."""
    pt = payload.get("type")
    if pt == "function_call":
        raw = payload.get("arguments")
        name = payload.get("name")
    elif pt == "custom_tool_call":
        raw = payload.get("input")
        name = payload.get("name")
    elif pt == "web_search_call":
        action = payload.get("action")
        raw = action.get("query") if isinstance(action, dict) else None
        name = "web_search"
    else:
        return None
    call = {"name": str(name or "tool")}
    if raw is not None and not isinstance(raw, str):
        raw = json.dumps(raw, ensure_ascii=False)
    if raw:
        call["input"] = redact(raw)[:MAX_TOOL_INPUT]
    if ts:
        call["at"] = ts
    return call


def parse_lines(lines):
    """Core parser over an iterable of JSONL strings (testable without a file)."""
    turns, tool_calls = [], []
    saw_known = False
    saw_any = False
    for line in lines:
        line = (line or "").strip()
        if not line:
            continue
        saw_any = True
        try:
            obj = json.loads(line)
        except Exception:
            continue  # partial/corrupt line -> skip, never crash
        if not isinstance(obj, dict):
            continue
        if obj.get("type") in _KNOWN_TYPES:
            saw_known = True
        if obj.get("type") != "response_item":
            continue  # only the API conversation carries turns/tool_calls
        payload = obj.get("payload")
        if not isinstance(payload, dict):
            continue
        ts = obj.get("timestamp") if isinstance(obj.get("timestamp"), str) else None
        pt = payload.get("type")
        if pt == "message":
            role = payload.get("role")
            if role not in ("user", "assistant"):  # drop developer/system messages
                continue
            text = _content_text(payload.get("content")).strip()
            if text:
                turn = {"role": role, "text": redact(text)}
                if ts:
                    turn["at"] = ts
                turns.append(turn)
        else:
            call = _tool_call(payload, ts)  # reasoning / *_output payloads -> None -> dropped
            if call:
                tool_calls.append(call)
    if saw_any and not saw_known:
        raise UnknownRolloutSchema("no recognizable Codex rollout lines")
    return {"turns": turns, "tool_calls": tool_calls}


def parse_transcript(path):
    """Parse a rollout file. Propagates UnknownRolloutSchema (caller skip-queues);
    a missing/unreadable file degrades to empty (never fatal)."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return parse_lines(f)
    except OSError:
        return {"turns": [], "tool_calls": []}


def _self_test():
    lines = [
        json.dumps({"timestamp": "2026-07-21T10:00:00.000Z", "type": "session_meta",
                    "payload": {"id": "019ef4ec-a261-7c62", "cwd": "/repo",
                                "git": {"repository_url": "git@github.com:kva/oida.git", "branch": "main"}}}),
        json.dumps({"timestamp": "2026-07-21T10:00:01.000Z", "type": "response_item",
                    "payload": {"type": "message", "role": "developer",
                                "content": [{"type": "input_text", "text": "SYSTEM PROMPT — must not appear"}]}}),
        json.dumps({"timestamp": "2026-07-21T10:00:02.000Z", "type": "response_item",
                    "payload": {"type": "message", "role": "user",
                                "content": [{"type": "input_text", "text": "Ship the codex connector"},
                                            {"type": "input_image", "image_url": "data:..."}]}}),
        json.dumps({"timestamp": "2026-07-21T10:00:03.000Z", "type": "response_item",
                    "payload": {"type": "reasoning", "encrypted_content": "OPAQUE-must-not-appear"}}),
        json.dumps({"timestamp": "2026-07-21T10:00:04.000Z", "type": "response_item",
                    "payload": {"type": "message", "role": "assistant",
                                "content": [{"type": "output_text", "text": "On it."}]}}),
        json.dumps({"timestamp": "2026-07-21T10:00:05.000Z", "type": "response_item",
                    "payload": {"type": "function_call", "name": "shell",
                                "arguments": "{\"cmd\": \"export TOKEN=ghp_0123456789abcdefghijklmnopqrstuvwx\"}"}}),
        json.dumps({"timestamp": "2026-07-21T10:00:06.000Z", "type": "response_item",
                    "payload": {"type": "function_call_output", "call_id": "x",
                                "output": "SECRET OUTPUT sk-ant-shouldnotappear"}}),
        json.dumps({"timestamp": "2026-07-21T10:00:07.000Z", "type": "response_item",
                    "payload": {"type": "web_search_call", "action": {"type": "search", "query": "codex rollout schema"}}}),
        json.dumps({"type": "event_msg", "payload": {"type": "user_message", "message": "duplicate of the user turn"}}),
        json.dumps({"type": "turn_context", "payload": {"model": "gpt-5.5"}}),
        "not json at all",
    ]
    out = parse_lines(lines)
    assert [t["role"] for t in out["turns"]] == ["user", "assistant"], out["turns"]
    assert out["turns"][0]["text"] == "Ship the codex connector", out["turns"]
    assert out["turns"][0]["at"] == "2026-07-21T10:00:02.000Z"
    # developer message + event_msg duplicate are both excluded (no double count)
    blob = json.dumps(out)
    assert "SYSTEM PROMPT" not in blob and "duplicate of the user turn" not in blob
    # tool calls: function_call (shell) + web_search_call, but NOT the outputs
    assert [c["name"] for c in out["tool_calls"]] == ["shell", "web_search"], out["tool_calls"]
    assert "ghp_0123456789" not in out["tool_calls"][0]["input"] and "«redacted" in out["tool_calls"][0]["input"]
    # reasoning + *_output bodies dropped -> their secrets never reach the envelope
    assert "OPAQUE-must-not-appear" not in blob and "sk-ant-shouldnotappear" not in blob and "SECRET OUTPUT" not in blob
    # unknown-schema detection: content present, but no known envelope types
    try:
        parse_lines([json.dumps({"foo": "bar"}), json.dumps({"baz": 1})])
        raise AssertionError("expected UnknownRolloutSchema")
    except UnknownRolloutSchema:
        pass
    # empty input is fine (not "unknown schema")
    assert parse_lines([]) == {"turns": [], "tool_calls": []}
    assert parse_lines(["", "   "]) == {"turns": [], "tool_calls": []}
    print("OK self-test: transcript (codex rollout / turns / tool_calls / outputs dropped / redacted / unknown-schema)")


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        print(json.dumps(parse_transcript(sys.argv[1]), indent=2))
