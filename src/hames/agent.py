"""Human-readable agent capsule loading."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import yaml
from pydantic import BaseModel, ConfigDict


class AgentMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    provider: str = "inherit"
    model: str = ""


@dataclass(frozen=True, slots=True)
class AgentCapsule:
    metadata: AgentMetadata
    instructions: str
    content_hash: str
    path: Path


def load_agent(path: Path) -> AgentCapsule:
    raw = path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{path}: AGENT.md must begin with YAML frontmatter")
    try:
        boundary = lines[1:].index("---") + 1
    except ValueError as exc:
        raise ValueError(f"{path}: unterminated YAML frontmatter") from exc
    metadata_raw = cast(object, yaml.safe_load("\n".join(lines[1:boundary])))
    metadata = AgentMetadata.model_validate(metadata_raw or {})
    instructions = "\n".join(lines[boundary + 1 :]).strip()
    if not instructions:
        raise ValueError(f"{path}: agent instructions cannot be empty")
    return AgentCapsule(
        metadata=metadata,
        instructions=instructions,
        content_hash=hashlib.sha256(raw.encode()).hexdigest(),
        path=path,
    )
