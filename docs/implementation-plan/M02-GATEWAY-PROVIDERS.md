# M02 — Local Provider Reliability and Gateway Hardening

## Goal

Harden the working M0/M1 gateway and local model providers so M03 can add tools
without first replacing their process or streaming contracts.

M02 keeps llama.cpp and Ollama as the concrete providers. Hosted OpenAI,
ChatGPT/Codex, Anthropic, and other services remain later work. Tool calls cross
the provider boundary in M02, but the harness records and rejects them rather than
executing them before the M03 policy gate exists.

## Named provider profiles

A provider profile is a user-selected identity; its adapter is the wire protocol
implementation. Multiple profiles may use the same adapter:

```toml
[runtime]
default_provider = "llama_cpp"

[providers.llama_cpp]
adapter = "llama_cpp"
base_url = "http://127.0.0.1:8080"
model = "qwen3.8-27b"
reasoning_effort = "medium"
supported_reasoning_efforts = ["low", "medium", "xhigh"]
timeout_seconds = 600.0

[providers.ollama]
adapter = "ollama"
base_url = "http://127.0.0.1:11434"
```

Session `provider` values store the profile ID. Existing `llama_cpp` and `ollama`
IDs remain valid, and their adapter is inferred when omitted by an older current
configuration. The older pre-rewrite `schema_version` format is still translated
in memory without rewriting the user's file.

Profile IDs, adapters, endpoints, defaults, effort declarations, and timeouts are
strictly validated. The default profile must exist.

## Protocol-v3 gateway

The persistent Python gateway remains loopback-only by default and is normally
started by the Rust client. It starts without contacting a model provider.

Relevant provider endpoints are:

```text
GET  /v1/health
GET  /v1/providers
POST /v1/providers/{profile_id}/probe
```

Health reports database readiness, configured profile IDs, the default profile,
and active-run count without performing network I/O. Provider listing returns
configuration only. Reachability and model discovery happen only through an
explicit probe.

The REPL probes its selected profile at startup. `/provider` probes the requested
profile, while `/status` deliberately probes every profile concurrently.

All non-health routes require the private local bearer token. Errors retain the
single typed envelope:

```json
{
  "error": {
    "code": "provider_timeout",
    "message": "...",
    "retryable": true,
    "details": {}
  }
}
```

Provider tracebacks, credentials, and wire objects do not cross this boundary.

## Capabilities and reasoning

llama.cpp discovery uses `/v1/models`, and `/props` is inspected only for loaded
or sleeping models so discovery does not wake unloaded router entries. Ollama
uses `/api/tags` and `/api/show`.

Reasoning is represented as a separate capability and channel. `off` disables it,
`on` enables boolean-only thinking, and named efforts are accepted only when the
model or profile declares them. Unknown values are rejected instead of silently
clamped.

M02 includes the known Qwen3.8 effort set `low`, `medium`, and `xhigh`. Ollama
thinking models default to boolean `on` unless a profile or known model family
declares levels; GPT-OSS uses `low`, `medium`, and `high`. A profile declaration
supports custom aliases and unloaded models without pretending all thinking
models share one scale.

## Provider request and stream contract

Provider input contains messages, system content, model, limits, optional
temperature, request metadata, and typed tool definitions. The normalized stream
contains:

```text
response.started
response.reasoning_delta
response.text_delta
response.tool_call_delta
response.usage
response.completed
```

Adapters raise a typed `ProviderError`; they do not emit a competing failure
terminal. The runtime requires exactly one start and completion, permits at most
one usage event, rejects events outside that order, and reports malformed streams
as `provider_protocol_error`.

llama.cpp completion is delayed until any late usage chunk and `[DONE]` have been
processed. Ollama completion is delayed until its NDJSON stream closes, allowing
data after a completed chunk to be detected. Provider retries remain disabled.

Tool-call name and JSON argument fragments are assembled across chunks. M02 emits
a durable `model.tool_call` with status `unhandled`, preserves partial model
output, and terminates with `unexpected_tool_call`. M03 will place execution behind
policy and approval.

Usage normalizes:

```text
input_tokens
output_tokens
cached_input_tokens nullable
reasoning_tokens nullable
provider_reported_cost nullable
```

Unreported cost is left null; Hames does not invent an exact cost.

## Durable and transient events

Durable events preserve requests, response start, usage, assembled reasoning and
answer output, assembled tool calls, and exactly one completed, failed, or
cancelled terminal. Token-sized reasoning, text, and tool-call deltas are transient.

Cancellation preserves partial output as interrupted, emits one `run.cancelled`,
and releases the session for another run. A client disconnect only removes its
subscription and never cancels model work.

SSE durable events carry their ledger sequence as `id`. Clients may resume with
`Last-Event-ID` or `after_sequence`; supplying different values is a typed error.
The broker subscribes before replay and deduplicates the replay/live boundary.
Slow clients cannot fail publishers, and the Rust client performs up to three
transport reconnections while reconciling partial display with durable output.

## Acceptance

Offline coverage includes strict configuration, profile compatibility, explicit
probing, provider fixtures, late usage, thinking channels, tool-call assembly,
timeouts, malformed streams, SSE cursor validation, replay, backpressure,
disconnect without cancellation, cancellation, partial output, and session-lock
release. No normal test requires a live or paid provider.

The manual gate uses an isolated `HAMES_HOME`, protocol v3, the live llama.cpp
router, and `qwen3.8-27b`. One session must successfully switch through `low`,
`medium`, and `xhigh`, after which the isolated gateway is stopped and its state is
discarded.

M02 is complete when the full Python and Rust quality gates pass, the manual
Qwen3.8 gate passes, the tree is clean, and annotated tag `m02` is created.

## Completion record

Completed on 2026-08-23 with protocol v3. The acceptance run used the live
`qwen3.8-27b` llama.cpp profile and returned the requested exact markers at all
three supported reasoning efforts. See
[`M2-QWEN38.md`](../model-evaluations/M2-QWEN38.md).
