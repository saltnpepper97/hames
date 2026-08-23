# M0 Qwen3.8 llama.cpp evaluation

Date: 2026-08-23

This is a live local-provider smoke result, not part of the offline test suite.

## Environment

- Provider: llama.cpp router at `http://127.0.0.1:8080`
- Model: `qwen3.8-27b`
- Requested reasoning effort: `low`
- Client path: Rust REPL → HTTP/SSE gateway → Python runtime → llama.cpp

llama.cpp's model-specific `/props` response advertised
`chat_template_caps.supports_reasoning_effort = true`. It did not advertise an
enumerated set of effort names, so the adapter exposes the Hames/Qwen-compatible
levels `low`, `medium`, and `xhigh`. Hames only requests `/props` for models the
router already reports as loaded or sleeping; discovery must not wake an unloaded
model and evict another one.

## Result

The live run demonstrated:

- automatic connection to a persistent gateway;
- model discovery without changing the loaded router model;
- separate streamed `thinking>` and `assistant>` channels;
- durable `assistant.reasoning`, `assistant.message`, completion, and usage events;
- a clean REPL exit while the gateway remained independently controllable.

An initial boundary probe correctly separated model output from harness authority,
but guessed definitions for future memory and Skill features that were not present
in its context. M0's core contract was tightened to forbid inventing unspecified
Hames behavior and to clarify that a working-directory path is not evidence of
file access. On the repeated probe, Qwen correctly stated that it had no tools or
filesystem access and declined to define the unspecified future concepts.

Ollama was not running on its default local port during this evaluation. Its
discovery, thinking, content, usage, error, and malformed-stream behavior remain
covered by offline adapter fixtures.
