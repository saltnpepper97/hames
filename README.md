# Hames

Hames is a local agent harness under active development. A trusted Python
backend owns the harness and gateway; a Rust REPL is the first client. M0 focuses
on proving local-model conversations and provenance before adding tools or rich
interfaces.

See [`docs/implementation-plan/README.md`](docs/implementation-plan/README.md)
for the milestone plan.

## Status

M0 implementation is in progress. llama.cpp and Ollama are the initial model
providers.

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

The backend diagnostic command is currently available as `uv run hamesd doctor`.
The user-facing `hames` executable is built from `crates/hames-repl`.

## License

Hames is available under the MIT License.
