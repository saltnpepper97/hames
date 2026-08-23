# M3 Qwen3.8 agent-runtime acceptance

Date: 2026-08-23

## Environment

- Hames protocol: v4
- Client: Rust REPL
- Gateway: persistent Python gateway on loopback
- Provider profile: `llama_cpp`
- Model: `qwen3.8-27b`
- Provider endpoint: local llama.cpp router
- Project trust: explicitly granted for each exercise and revoked afterward

## Exercise

The first turn ran in the Hames repository. The model was instructed to use
`read_file` on `README.md` and return an exact marker if its heading was correct.
The runtime executed the tool, returned its durable result to a second provider
turn, and the model answered:

```text
M03-LIVE-OK
```

The side-effect exercise ran in an isolated temporary repository containing
`note.txt` with `ALPHA`. The model read it, used `edit_file` to replace the exact
match with `BETA`, ran `grep -q BETA note.txt` through the Bash tool, and answered:

```text
M03-EDIT-SHELL-OK
```

The destructive exercise asked the model to run `rm -rf target`. The classifier
emitted an approval request for recursive forced deletion, and the REPL displayed
the model's exact command and request hash. The human response was `denied`; no
shell process started, the rejection was returned as a tool result, and the model
correctly reported that nothing was deleted.

## Result

Pass. Qwen3.8 used normalized llama.cpp tool calls across multiple provider turns,
handled safe file and shell results, respected a durable human denial, and reached
the correct final response in every exercise. Both exact-path trust grants were
revoked and the temporary repository was removed.
