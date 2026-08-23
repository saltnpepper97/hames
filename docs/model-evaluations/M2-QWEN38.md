# M2 Qwen3.8 provider acceptance

Date: 2026-08-23

## Environment

- Hames protocol: v3
- Client: Rust REPL
- Gateway: persistent Python gateway on an isolated loopback port
- Provider profile: `llama_cpp`
- Model: `qwen3.8-27b`
- Provider endpoint: local llama.cpp router
- State: isolated temporary `HAMES_HOME`, removed after the run

## Exercise

One session was created in the repository working directory. It began at `low`
reasoning, then changed through the REPL to `medium` and `xhigh`. Each turn asked
for one exact marker and no other answer text.

Observed final answers:

```text
LOW-OK
MEDIUM-OK
XHIGH-OK
```

Reasoning and answer content remained separate in the REPL at every level. The
session retained `xhigh` as its final setting, protocol-v3 gateway status remained
healthy after the three runs, and the gateway stopped normally.

## Result

Pass. The live provider accepted all three Qwen3.8 effort values used by Hames,
streamed distinct reasoning and answer channels, completed each run, and preserved
the updated session setting.
