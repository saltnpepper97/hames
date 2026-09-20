"""User-owned slash command definitions; no model routes are built into clients."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RESERVED = set(
    """
new clear sessions resume queue tasks plan dream compact goal fork connect model provider effort
reasoning agent mode themes status project gateway events inspect context details
memory skills scars
plugins mcp help cancel quit stop heal flow
""".split()
)


class UserCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    description: str = Field(min_length=1)
    action: Literal["execute_plan"] = "execute_plan"
    agent: str = Field(min_length=1)
    source: str = ""


def load_commands(root: Path, workspace: Path) -> list[UserCommand]:
    commands: dict[str, UserCommand] = {}
    for directory in (root / "commands", workspace / ".hames" / "commands"):
        for path in sorted(directory.glob("*.toml")):
            name = path.stem
            if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name) or name in RESERVED:
                raise ValueError(f"{path}: invalid or reserved command name")
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
            if "name" in raw or "source" in raw:
                raise ValueError(f"{path}: name comes from filename; source is read-only")
            commands[name] = UserCommand(name=name, source=str(path), **raw)
    return sorted(commands.values(), key=lambda command: command.name)
