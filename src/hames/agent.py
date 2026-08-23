"""Human-editable, portable agent capsules and their private registry."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AGENT_ID = re.compile(r"[a-z][a-z0-9-]{0,62}")
READ_ONLY_TOOLS = frozenset({"read_file", "list_dir"})


class AgentTools(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self) -> AgentTools:
        if len(self.allow) != len(set(self.allow)) or len(self.deny) != len(set(self.deny)):
            raise ValueError("agent tool allow and deny lists must not contain duplicates")
        return self


class DelegationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: bool = False
    allowed_agents: list[str] = Field(default_factory=list)

    @field_validator("allowed_agents")
    @classmethod
    def valid_targets(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("delegation allowed_agents must not contain duplicates")
        if any(AGENT_ID.fullmatch(value) is None for value in values):
            raise ValueError("delegation allowed_agents contains an invalid agent ID")
        return values

    @model_validator(mode="after")
    def targets_need_permission(self) -> DelegationPolicy:
        if not self.allow and self.allowed_agents:
            raise ValueError("delegation allowed_agents requires delegation.allow: true")
        return self


class AgentMetadata(BaseModel):
    """The M05 capsule contract. Provider/model legacy fields are inert compatibility input."""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str = Field(min_length=1, max_length=80)
    tools: AgentTools = Field(default_factory=AgentTools)
    authority: Literal["standard", "read_only"] = "standard"
    delegation: DelegationPolicy = Field(default_factory=DelegationPolicy)
    provider: str | None = None
    model: str | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        if AGENT_ID.fullmatch(value) is None:
            raise ValueError("agent ID must match [a-z][a-z0-9-]{0,62}")
        return value


@dataclass(frozen=True, slots=True)
class AgentCapsule:
    metadata: AgentMetadata
    instructions: str
    content_hash: str
    path: Path

    @property
    def deprecated_fields(self) -> list[str]:
        return [name for name in ("provider", "model") if getattr(self.metadata, name) is not None]


@dataclass(frozen=True, slots=True)
class AgentSummary:
    id: str
    name: str
    authority: str
    path: Path
    content_hash: str


class AgentRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.retired_root = root / ".retired"

    def path_for(self, agent_id: str) -> Path:
        _validate_id(agent_id)
        return self.root / agent_id / "AGENT.md"

    def load(self, agent_id: str) -> AgentCapsule:
        capsule = load_agent(self.path_for(agent_id))
        if capsule.metadata.id != agent_id:
            raise ValueError(f"{capsule.path}: frontmatter id does not match its directory")
        return capsule

    def list(self) -> list[AgentSummary]:
        if not self.root.exists():
            return []
        values: list[AgentSummary] = []
        for directory in sorted(self.root.iterdir(), key=lambda item: item.name):
            if directory.name.startswith(".") or not directory.is_dir():
                continue
            path = directory / "AGENT.md"
            if not path.is_file():
                continue
            capsule = self.load(directory.name)
            values.append(
                AgentSummary(
                    id=capsule.metadata.id,
                    name=capsule.metadata.name,
                    authority=capsule.metadata.authority,
                    path=path,
                    content_hash=capsule.content_hash,
                )
            )
        return values

    def create(self, agent_id: str, name: str, *, authority: str = "standard") -> AgentCapsule:
        _validate_id(agent_id)
        if authority not in {"standard", "read_only"}:
            raise ValueError("authority must be standard or read_only")
        path = self.path_for(agent_id)
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
        path.parent.chmod(0o700)
        raw = (
            "---\n"
            f"id: {agent_id}\n"
            f"name: {name}\n"
            f"authority: {authority}\n"
            "---\n"
            f"You are {name}. Follow the assigned task carefully and report evidence clearly.\n"
        )
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return self.load(agent_id)

    def retire(self, agent_id: str) -> Path:
        if agent_id == "default":
            raise ValueError("the default agent cannot be retired")
        source = self.path_for(agent_id).parent
        if not source.is_dir():
            raise FileNotFoundError(source)
        self.retired_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.retired_root.chmod(0o700)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        destination = self.retired_root / f"{agent_id}-{stamp}"
        if destination.exists():
            raise FileExistsError(destination)
        shutil.move(str(source), destination)
        return destination


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


def _validate_id(agent_id: str) -> None:
    if AGENT_ID.fullmatch(agent_id) is None:
        raise ValueError("agent ID must match [a-z][a-z0-9-]{0,62}")


def permitted_tools(capsule: AgentCapsule, available: set[str]) -> frozenset[str]:
    """Intersect a capsule's declared authority with the harness tool registry."""

    permitted = set(available)
    if capsule.metadata.authority == "read_only":
        permitted.intersection_update(READ_ONLY_TOOLS)
    if capsule.metadata.tools.allow:
        permitted.intersection_update(capsule.metadata.tools.allow)
    permitted.difference_update(capsule.metadata.tools.deny)
    return frozenset(permitted)
