# Codex rollout JSONL — verified schema

**Verified 2026-07-21** against 82 real rollout files in `~/.codex/sessions`, spanning
cli_version dates 2026-01 → 2026-07. The core envelope was **stable across that range**
(only peripheral `turn_context`/`event_msg` payloads gained fields; the parser ignores those).
This file is the build-time schema record the session-ingestion plan requires. **Re-verify
before shipping a new client version** — the format drifts; on an unrecognized schema the
parser routes the session to the skip queue and never crashes (see `engine/transcript.py`).

## File layout

```
~/.codex/sessions/YYYY/MM/DD/rollout-<ISO8601>-<uuid7>.jsonl
```
One JSONL file per session. The `<uuid7>` in the filename is the session id and also
appears as `session_meta.payload.id`.

## Line envelope

Every line: `{ "timestamp": <ISO>, "type": <string>, "payload": <object> }`. Four `type`s:

| `type` | meaning | used for |
|---|---|---|
| `session_meta` | line 0 — session header | session id, cwd, git repo/branch, start time |
| `response_item` | the Responses-API conversation | turns + tool-call log |
| `event_msg` | TUI telemetry (duplicates the conversation) | **ignored** (avoids double counting) |
| `turn_context` | per-turn config (model, sandbox, …) | **ignored** |

### `session_meta.payload`
```
id             session uuid (stable identity for update/erase)
cwd            working dir at session start
timestamp      session start (ISO)
git.repository_url   git remote  -> owner/repo (session-time truth)
git.branch           branch
cli_version, originator, model_provider, base_instructions.text  (not ingested)
```

### `response_item.payload` — discriminated by `payload.type`
| `payload.type` | shape | mapped to |
|---|---|---|
| `message` | `{ role, content:[{type,text}] }`, role ∈ user/assistant/**developer** | **turn** (user/assistant only; developer dropped) |
| `function_call` | `{ name, arguments }` | **tool_call** (name + redacted/truncated `arguments`) |
| `custom_tool_call` | `{ name, input }` | **tool_call** (name + redacted/truncated `input`) |
| `web_search_call` | `{ action:{query} }` | **tool_call** (name `web_search`, query as input) |
| `function_call_output` | `{ call_id, output }` | **DROPPED** — tool result body |
| `custom_tool_call_output` | `{ call_id, output }` | **DROPPED** — tool result body |
| `reasoning` | `{ summary, encrypted_content }` | **DROPPED** — encrypted model CoT |

Message `content` parts carry text under `text` (part types `input_text` / `output_text`);
`input_image` parts have no `text` and are skipped. The parser is liberal — it takes any part
with a string `text` — so a new text-part name still works.

## Why outputs and reasoning are dropped

Same deliberate reduction as the Claude client: tool-**result** bodies are ~90% of transcript
bytes and the biggest secret-leak surface, so only the tool *name* + a redacted, truncated
*input* survive. `reasoning` is encrypted and not conversational. Everything that does survive
is passed through `engine/redact.py` before it leaves the machine.

## Mapping to the wire contract

The parser output `{turns, tool_calls}` feeds the exact same `SessionEnvelope` the Claude client
sends (`packages/ingestion/src/sessionEnvelope.ts`, `SESSION_SURFACES` already includes
`codex_cli`), with `surface: "codex_cli"`. Validated: 8/8 envelopes built from real sessions
pass `SessionEnvelopeSchema` (2026-07-21).
