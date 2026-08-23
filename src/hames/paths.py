"""Private persistent path resolution for Hames."""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DEFAULT_AGENT = """---
id: default
name: Hames
provider: inherit
model: ""
---
You are the default Hames agent. Be direct, careful, and honest about what the
supplied context establishes. Clearly distinguish your reasoning and proposed
output from actions or durable state owned by the Hames harness. When Hames has
not defined a product-specific concept for you, say so instead of inventing it.
"""


@dataclass(frozen=True, slots=True)
class HamesPaths:
    """All persistent Hames paths derived from one private root."""

    root: Path

    @classmethod
    def resolve(
        cls,
        *,
        environ: Mapping[str, str] | None = None,
        root: Path | None = None,
    ) -> HamesPaths:
        env = os.environ if environ is None else environ
        selected = root or Path(env.get("HAMES_HOME", Path.home() / ".hames"))
        return cls(selected.expanduser().resolve(strict=False))

    @property
    def config_file(self) -> Path:
        return self.root / "config.toml"

    @property
    def database(self) -> Path:
        return self.root / "hames.db"

    @property
    def agents(self) -> Path:
        return self.root / "agents"

    @property
    def default_agent(self) -> Path:
        return self.agents / "default" / "AGENT.md"

    @property
    def blobs(self) -> Path:
        return self.root / "blobs"

    @property
    def flows(self) -> Path:
        return self.root / "flows"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def gateway_pid(self) -> Path:
        return self.runtime / "gateway.pid"

    @property
    def gateway_token(self) -> Path:
        return self.runtime / "gateway.token"

    @property
    def repl_history(self) -> Path:
        return self.root / "repl-history"

    def ensure_foundation(self) -> None:
        """Create only the directories and files required by M0."""

        for directory in (self.root, self.agents, self.agents / "default", self.logs, self.runtime):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)

        if not self.default_agent.exists():
            self.default_agent.write_text(DEFAULT_AGENT, encoding="utf-8")
            self.default_agent.chmod(0o600)

        if not self.gateway_token.exists():
            self.gateway_token.write_text(secrets.token_urlsafe(32), encoding="utf-8")
            self.gateway_token.chmod(0o600)

    def read_gateway_token(self) -> str:
        return self.gateway_token.read_text(encoding="utf-8").strip()
