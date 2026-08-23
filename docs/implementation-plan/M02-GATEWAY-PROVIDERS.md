# M02 — Local Gateway, Streaming Transport, and Model Providers

## Goal

Create the process boundary through which all clients operate Hames, and implement real model streaming with normalized provider events.

At the end of M02, a client can create a session, submit a message, receive streamed model output over SSE, cancel a request, and inspect provider usage. There are not yet tools or a full agent loop.

## Architecture

```text
client
  │ HTTP commands
  │ SSE events
  ▼
gateway
  ▼
model request service
  ▼
provider adapter
  ▼
event ledger
```

The REPL in M03 and web UI in M04/M10 must use this same gateway contract rather than importing hidden runtime internals.

## Gateway lifecycle

Implement:

```bash
hames serve
```

Default bind:

```text
127.0.0.1:7411
```

Do not bind non-loopback by default.

Expose a health endpoint that distinguishes:

- process alive;
- database ready;
- provider configured;
- provider reachable only when explicitly probed.

Startup must not make an external provider request.

## HTTP API v1

Required commands:

```text
POST /v1/sessions
GET  /v1/sessions
GET  /v1/sessions/{id}
GET  /v1/sessions/{id}/events
POST /v1/sessions/{id}/messages
POST /v1/runs/{run_id}/cancel
GET  /v1/health
GET  /v1/providers
```

Streaming endpoint:

```text
GET /v1/events?session_id=<id>
Accept: text/event-stream
```

Support `Last-Event-ID` or equivalent `after_sequence` so clients reconnect without losing durable events.

A disconnected client must not imply cancelled model work. Cancellation is explicit.

## Gateway schemas

Public requests/responses use versioned typed schemas.

Errors use one envelope:

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

Do not return raw Python tracebacks to clients.

## Provider protocol

Define a minimal provider interface around model requests, not provider-specific SDK objects.

Provider input includes:

```text
model
messages/content blocks
system content
tool schemas (empty in M02 but supported by type)
temperature/limits where supported
request metadata
```

Normalized streaming events:

```text
response.started
response.text_delta
response.tool_call_delta
response.reasoning_delta optional
response.usage
response.completed
response.failed
```

Provider-specific data may be attached under an opaque debug field that is not required for runtime correctness.

## Concrete providers

### 1. OpenAI-compatible provider

Support configurable:

- base URL;
- API key via environment/config secret reference;
- model;
- timeout;
- optional custom headers.

It must work with local OpenAI-compatible servers as well as compatible hosted endpoints.

### 2. Anthropic provider

Support:

- API key via secret reference;
- model;
- streaming;
- usage;
- tool-call shape normalization even though tools are not yet executed.

Provider objects must not leak beyond the adapter.

### 3. Fake provider

A deterministic provider used by all tests.

It can emit scripted deltas, usage, errors, stalls, malformed responses, and cancellation behavior.

## Model request events

Add typed ledger events:

```text
model.requested
model.response.started
model.response.delta optional persistence policy
model.usage
model.response.completed
model.response.failed
run.cancelled
```

Avoid persisting thousands of tiny deltas if unnecessary. The durable ledger must preserve the final response and important timing boundaries; live deltas can be transient gateway events. Document exactly which streaming events are durable versus ephemeral.

Provider-reported usage is authoritative for aggregate usage when present.

Normalize:

```text
input_tokens
output_tokens
cached_input_tokens nullable
reasoning_tokens nullable
provider_reported_cost nullable
```

Do not invent exact cost when the provider does not report it. Estimated cost may be computed later and must be labeled as estimated.

## Cancellation and timeout behavior

Cancellation must:

1. mark the run cancelling;
2. cancel the provider request;
3. emit `run.cancelled`;
4. preserve partial generated output as interrupted output;
5. never leave the session locked.

Timeouts are typed provider failures.

Retries are disabled by default in this milestone. If a transport retry is implemented, it may occur only before any response body is accepted and must never duplicate logical requests silently.

## Authentication posture

Because v0.1 is local-only:

- loopback binding by default;
- reject non-loopback bind unless explicitly configured;
- generate/use a local bearer token when remote/non-loopback binding is enabled;
- never place token in normal logs.

## Tests

Cover:

- route schemas;
- session creation;
- SSE connection and resume after sequence;
- fake provider streamed text;
- persisted final assistant response;
- provider usage recording;
- explicit cancellation;
- client disconnect without cancellation;
- timeout;
- malformed provider event;
- OpenAI-compatible fixture normalization;
- Anthropic fixture normalization;
- redaction of provider auth material;
- non-loopback bind protection;
- migration from M01.

No live network provider is required by normal tests.

## Manual smoke test

With a configured local OpenAI-compatible model:

1. start `hames serve`;
2. create a session;
3. subscribe to SSE;
4. submit a message;
5. observe streamed output;
6. reconnect using last event sequence;
7. confirm provider usage in ledger.

## Commit expectations

Suggested slices:

1. gateway process/API schemas;
2. SSE broker and resume;
3. provider protocol + fake provider;
4. OpenAI-compatible adapter;
5. Anthropic adapter;
6. cancellation/usage/security/docs.

## Acceptance gate

M02 is complete when the gateway can reliably stream a real configured model and a deterministic fake model, survive reconnects, cancel work, persist final responses and usage, expose typed errors, and pass all offline tests.

Finish with a clean tree and annotated tag `m02`.
