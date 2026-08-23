# Hames

Hames is a local agent harness under active development. A trusted Python
backend owns the harness and gateway; a Rust REPL is the first client. M0 focuses
on proving local-model conversations and provenance before adding tools or rich
interfaces.

See [`docs/implementation-plan/README.md`](docs/implementation-plan/README.md)
for the milestone plan.

## Status

M0 is implemented. It provides a persistent loopback gateway, a Rust conversation
REPL, append-only SQLite provenance, separate reasoning/answer streaming, and
native llama.cpp and Ollama adapters. M0 intentionally exposes no tools to the
model; file and shell work begin in M3 after the policy boundary exists.

## Quick start

Install the locked Python environment and build the Rust client:

```bash
uv sync --locked
cargo build --locked
```

Run the development binary from the repository:

```bash
target/debug/hames -V
target/debug/hames doctor
target/debug/hames
```

The REPL starts the Python gateway on demand. Exiting the REPL leaves it running;
use `target/debug/hames gateway status` or `target/debug/hames gateway stop` to
inspect or stop it.

Hames defaults to llama.cpp at `http://127.0.0.1:8080`. If exactly one model is
reported it is selected automatically; otherwise the REPL asks. A minimal
`~/.hames/config.toml` override looks like:

```toml
[runtime]
default_provider = "llama_cpp" # or "ollama"

[providers.llama_cpp]
base_url = "http://127.0.0.1:8080"
model = "qwen3.8-27b"
reasoning_effort = "medium"

[providers.ollama]
base_url = "http://127.0.0.1:11434"
```

Every setting can be overridden with a nested environment name such as
`HAMES_PROVIDERS__LLAMA_CPP__MODEL=qwen3.8-27b`. `HAMES_HOME` relocates all
persistent state from its private default at `~/.hames`; it is particularly useful
for isolated tests.

Inside the REPL, `/help` lists session, provider, model, status, and reasoning
commands. A trailing `\` continues input on the next line. Ctrl-C during a model
run requests cancellation; Ctrl-D or `/quit` exits the client.

## Development

Install locked dependencies and run the current checks with:

```bash
uv sync --locked
cargo build --locked
uv run ruff format --check .
uv run ruff check .
uv run pyright
uv run pytest
cargo fmt --all --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

The backend diagnostic command is also available as `uv run hamesd doctor`. The
user-facing `hames` executable is built from `crates/hames-repl`.

The extracted and refined implementation plan lives in
[`docs/implementation-plan/`](docs/implementation-plan/). The original planning
archive remains untouched at the repository root.

## License

Hames is available under the MIT License.
