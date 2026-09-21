"""Human-editable, portable agent capsules and their private registry."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

import yaml
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)

AGENT_ID = re.compile(r"[a-z][a-z0-9-]{0,62}")
_NON_SLUG = re.compile(r"[^a-z0-9]+")
_AVATAR_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
READ_ONLY_TOOLS = frozenset(
    {
        "ask_user",
        "spawn_agent",
        "read_file",
        "list_dir",
        "vcs_inspect",
        "skill_load",
        "memory_search",
        "scar_list",
        "skill_catalog",
        "session_title_set",
        "task_list",
        "task_update",
        "web_search",
        "web_fetch",
        "mcp_resource_list",
        "mcp_resource_read",
    }
)
DEFAULT_INSTRUCTIONS = (
    "You are {name}. Follow the assigned task carefully and report evidence clearly."
)


class AgentTools(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique(self) -> AgentTools:
        if len(self.allow) != len(set(self.allow)) or len(self.deny) != len(set(self.deny)):
            raise ValueError("agent tool allow and deny lists must not contain duplicates")
        return self


class AgentSkills(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    pin: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_and_reducible(self) -> AgentSkills:
        for label, values in (
            ("allow", self.allow),
            ("deny", self.deny),
            ("pin", self.pin),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"agent skill {label} list must not contain duplicates")
        deny = set(self.deny)
        allow = set(self.allow)
        if deny & allow:
            raise ValueError("agent skill allow and deny lists must be disjoint")
        if deny & set(self.pin):
            raise ValueError("agent skill pin list cannot include denied skills")
        if allow and any(slug not in allow for slug in self.pin):
            raise ValueError("agent skill pin list must be a subset of allow when allow is set")
        return self


class DelegationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: bool = True
    allowed_agents: list[str] = Field(default_factory=list)
    coordinator_only: bool = False

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
        if self.coordinator_only and not self.allow:
            raise ValueError("coordinator_only requires delegation.allow: true")
        if not self.allow and self.allowed_agents:
            raise ValueError("delegation allowed_agents requires delegation.allow: true")
        return self


class AgentAvatar(BaseModel):
    """Portable visual identity used by Hames clients."""

    model_config = ConfigDict(extra="forbid")

    shape: Literal["circle", "square", "triangle", "cloud", "hex", "drop"] = "circle"
    eyes: Literal["dots", "visor", "pill"] = "dots"
    face: Literal["solid", "none"] = "solid"
    color: str = "#64748b"

    @field_validator("shape", mode="before")
    @classmethod
    def migrate_early_shapes(cls, value: object) -> object:
        # These values briefly shipped during the Web avatar prototype. Keep
        # their capsules loadable while presenting only the refined shape set.
        if value in {"round", "arch"}:
            return "circle" if value == "round" else "triangle"
        if value == "capsule":
            return "cloud"
        return value

    @field_validator("eyes", mode="before")
    @classmethod
    def migrate_early_eyes(cls, value: object) -> object:
        return "pill" if value == "happy" else value

    @field_validator("face", mode="before")
    @classmethod
    def migrate_early_faces(cls, value: object) -> object:
        return "solid" if value == "outline" else value

    @field_validator("color")
    @classmethod
    def valid_color(cls, value: str) -> str:
        if _AVATAR_COLOR.fullmatch(value) is None:
            raise ValueError("agent avatar color must be a six-digit hex color")
        return value.lower()


class AgentExecution(BaseModel):
    """Human-configured execution selection; never supplied by a delegated model."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    reasoning_effort: str = ""


class AgentMetadata(BaseModel):
    """The M05 capsule contract. Provider/model legacy fields are inert compatibility input."""

    model_config = ConfigDict(extra="forbid")

    id: str
    slug: str = ""
    aliases: list[str] = Field(default_factory=list)
    name: str = Field(min_length=1, max_length=80)
    tools: AgentTools = Field(default_factory=AgentTools)
    skills: AgentSkills = Field(default_factory=AgentSkills)
    authority: Literal["standard", "read_only"] = "standard"
    delegation: DelegationPolicy = Field(default_factory=DelegationPolicy)
    avatar: AgentAvatar | None = None
    execution: AgentExecution | None = Field(
        default=None,
        validation_alias=AliasChoices("default_model", "execution"),
        serialization_alias="default_model",
    )
    provider: str | None = None
    model: str | None = None

    @field_validator("slug")
    @classmethod
    def valid_slug(cls, value: str) -> str:
        if value:
            _validate_id(value)
        return value

    @field_validator("aliases")
    @classmethod
    def valid_aliases(cls, values: list[str]) -> list[str]:
        for value in values:
            _validate_id(value)
        return list(dict.fromkeys(values))

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
    avatar: AgentAvatar | None
    slug: str = ""
    aliases: tuple[str, ...] = ()


class AgentRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.retired_root = root / ".retired"

    def path_for(self, agent_id: str) -> Path:
        _validate_id(agent_id)
        return self.root / agent_id / "AGENT.md"

    def load(self, agent_id: str) -> AgentCapsule:
        path = self.path_for(agent_id)
        if not path.is_file() and self.root.exists():
            for candidate in self.root.glob("*/AGENT.md"):
                if candidate.parent.name.startswith("."):
                    continue
                metadata = load_agent(candidate).metadata
                if agent_id in {metadata.slug, metadata.id, *metadata.aliases}:
                    path = candidate
                    break
        capsule = load_agent(path)
        if path.parent.name not in {
            capsule.metadata.id,
            capsule.metadata.slug,
            *capsule.metadata.aliases,
        }:
            raise ValueError(f"{capsule.path}: frontmatter id or slug does not match its directory")
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
                    slug=capsule.metadata.slug or capsule.metadata.id,
                    name=capsule.metadata.name,
                    authority=capsule.metadata.authority,
                    path=path,
                    content_hash=capsule.content_hash,
                    avatar=capsule.metadata.avatar,
                    aliases=tuple(capsule.metadata.aliases),
                )
            )
        return values

    def taken_ids(self) -> set[str]:
        if not self.root.exists():
            return set()
        return {
            item.name
            for item in self.root.iterdir()
            if item.is_dir() and not item.name.startswith(".")
        }

    def create(
        self,
        name: str | None = None,
        *,
        authority: str = "standard",
        source: str | None = None,
    ) -> AgentCapsule:
        if authority not in {"standard", "read_only"}:
            raise ValueError("authority must be standard or read_only")
        if name is not None and not name.strip():
            name = None
        source_id = None
        source_name = None
        source_authority = None
        body = ""
        tools = AgentTools()
        skills = AgentSkills()
        delegation = DelegationPolicy()
        avatar = None
        execution = None
        aliases: list[str] = []
        if source is not None:
            metadata_raw, body = _split_agent_markdown(source)
            if "id" in metadata_raw and metadata_raw["id"] is not None:
                source_id = str(metadata_raw["id"])
            if "name" in metadata_raw and metadata_raw["name"] is not None:
                source_name = str(metadata_raw["name"])
            if "authority" in metadata_raw and metadata_raw["authority"] is not None:
                source_authority = str(metadata_raw["authority"])
            tools = AgentTools.model_validate(metadata_raw.get("tools") or {})
            skills = AgentSkills.model_validate(metadata_raw.get("skills") or {})
            delegation = DelegationPolicy.model_validate(metadata_raw.get("delegation") or {})
            aliases = AgentMetadata.valid_aliases(
                TypeAdapter(list[str]).validate_python(metadata_raw.get("aliases") or [])
            )
            if metadata_raw.get("avatar") is not None:
                avatar = AgentAvatar.model_validate(metadata_raw["avatar"])
            selection = metadata_raw.get("default_model", metadata_raw.get("execution"))
            if selection is not None:
                execution = AgentExecution.model_validate(selection)
            extra = set(metadata_raw) - {
                "id",
                "slug",
                "aliases",
                "name",
                "authority",
                "tools",
                "skills",
                "delegation",
                "avatar",
                "provider",
                "model",
                "execution",
                "default_model",
            }
            if extra:
                raise ValueError(f"unknown AGENT.md frontmatter key: {sorted(extra)[0]}")
        chosen_authority = source_authority or authority
        if chosen_authority not in {"standard", "read_only"}:
            raise ValueError("authority must be standard or read_only")
        agent_id, display = allocate_agent_identity(
            name=name or source_name,
            agent_id=source_id,
            taken=self.taken_ids()
            | {
                value
                for agent in self.list()
                for value in (agent.id, agent.slug, *agent.aliases)
                if value
            },
        )
        path = self.path_for(agent_id)
        taken = {value for agent in self.list() for value in (agent.id, agent.slug, *agent.aliases)}
        if taken.intersection(aliases):
            raise ValueError("agent alias is already in use")
        if path.exists():
            raise FileExistsError(path)
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
        path.parent.chmod(0o700)
        instructions = body.strip() or DEFAULT_INSTRUCTIONS.format(name=display)
        payload: dict[str, object] = {
            "id": agent_id,
            "name": display,
            "authority": chosen_authority,
        }
        if aliases:
            payload["aliases"] = aliases
        delegation = self._canonical_delegation(delegation)
        if tools.allow or tools.deny:
            payload["tools"] = tools.model_dump(mode="json", exclude_defaults=True)
        if skills.allow or skills.deny or skills.pin:
            payload["skills"] = skills.model_dump(mode="json", exclude_defaults=True)
        if delegation != DelegationPolicy():
            payload["delegation"] = delegation.model_dump(mode="json", exclude_defaults=True)
        if avatar is not None:
            payload["avatar"] = avatar.model_dump(mode="json")
        if execution is not None:
            payload["default_model"] = execution.model_dump(mode="json")
        raw = f"---\n{yaml.safe_dump(payload, sort_keys=False)}---\n{instructions}\n"
        AgentMetadata.model_validate(payload)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        return self.load(agent_id)

    def retire(self, agent_id: str) -> Path:
        capsule = self.load(agent_id)
        agent_id = capsule.metadata.id
        if agent_id == "default":
            raise ValueError("the default agent cannot be retired")
        source = capsule.path.parent
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

    def update(
        self,
        agent_id: str,
        *,
        name: str | None = None,
        instructions: str | None = None,
        tools: AgentTools | None = None,
        skills: AgentSkills | None = None,
        avatar: AgentAvatar | None = None,
        source: str | None = None,
        default_model: AgentExecution | None = None,
        update_default_model: bool = False,
    ) -> AgentCapsule:
        """Atomically update a capsule without changing its stable identity."""

        current = self.load(agent_id)
        agent_id = current.metadata.id
        if source is not None:
            if update_default_model or any(
                value is not None for value in (name, instructions, tools, skills, avatar)
            ):
                raise ValueError("source cannot be combined with structured agent updates")
            raw = source
        else:
            if not update_default_model and all(
                value is None for value in (name, instructions, tools, skills, avatar)
            ):
                raise ValueError("agent update requires source or a structured field")
            metadata_raw, current_instructions = _split_agent_markdown(
                current.path.read_text(encoding="utf-8"), origin=str(current.path)
            )
            if update_default_model:
                metadata_raw.pop("execution", None)
                metadata_raw.pop("default_model", None)
                if default_model is not None:
                    metadata_raw["default_model"] = default_model.model_dump(mode="json")
            if name is not None:
                stripped_name = name.strip()
                if not stripped_name:
                    raise ValueError("agent name cannot be empty")
                metadata_raw["name"] = stripped_name
            if tools is not None:
                metadata_raw["tools"] = tools.model_dump(mode="json")
            if skills is not None:
                metadata_raw["skills"] = skills.model_dump(mode="json")
            if avatar is not None:
                metadata_raw["avatar"] = avatar.model_dump(mode="json")
            body = current_instructions if instructions is None else instructions.strip()
            raw = f"---\n{yaml.safe_dump(metadata_raw, sort_keys=False)}---\n{body}\n"

        candidate = _load_agent_source(raw, current.path)
        if candidate.metadata.name != current.metadata.name or (
            name is not None and not current.metadata.slug
        ):
            taken = {
                value
                for agent in self.list()
                if agent.id != agent_id
                for value in (agent.id, agent.slug or agent.id, *agent.aliases)
            }
            slug, _ = allocate_agent_identity(
                name=candidate.metadata.name, agent_id=None, taken=taken
            )
            metadata_raw, body = _split_agent_markdown(raw)
            metadata_raw["slug"] = slug
            raw = f"---\n{yaml.safe_dump(metadata_raw, sort_keys=False)}---\n{body}\n"
            candidate = _load_agent_source(raw, current.path)
        if candidate.metadata.id != agent_id:
            raise ValueError("agent ID cannot be changed")
        aliases = list(
            dict.fromkeys(
                [
                    *current.metadata.aliases,
                    *candidate.metadata.aliases,
                    current.metadata.slug or agent_id,
                    current.path.parent.name,
                ]
            )
        )
        aliases = [alias for alias in aliases if alias not in {agent_id, candidate.metadata.slug}]
        for agent in self.list():
            if agent.id != agent_id and {candidate.metadata.slug, *aliases}.intersection(
                {agent.id, agent.slug, *agent.aliases}
            ):
                raise ValueError("agent slug or alias is already in use")
        metadata_raw, body = _split_agent_markdown(raw)
        if aliases:
            metadata_raw["aliases"] = aliases
        policy = self._canonical_delegation(candidate.metadata.delegation)
        if "delegation" in metadata_raw:
            metadata_raw["delegation"] = policy.model_dump(mode="json", exclude_defaults=True)
        raw = f"---\n{yaml.safe_dump(metadata_raw, sort_keys=False)}---\n{body}\n"
        _load_agent_source(raw, current.path)
        _atomic_replace(current.path, raw)
        return self.load(agent_id)

    def _canonical_delegation(self, policy: DelegationPolicy) -> DelegationPolicy:
        targets: list[str] = []
        for target in policy.allowed_agents:
            try:
                target = self.load(target).metadata.id
            except FileNotFoundError:
                pass  # Allow definitions that refer to agents created later.
            if target not in targets:
                targets.append(target)
        return policy.model_copy(update={"allowed_agents": targets})


def load_agent(path: Path) -> AgentCapsule:
    raw = path.read_text(encoding="utf-8")
    return _load_agent_source(raw, path)


def _load_agent_source(raw: str, path: Path) -> AgentCapsule:
    metadata_raw, instructions = _split_agent_markdown(raw, origin=str(path))
    metadata = AgentMetadata.model_validate(metadata_raw)
    if not instructions:
        raise ValueError(f"{path}: agent instructions cannot be empty")
    return AgentCapsule(
        metadata=metadata,
        instructions=instructions,
        content_hash=hashlib.sha256(raw.encode()).hexdigest(),
        path=path,
    )


def _atomic_replace(path: Path, raw: str) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".AGENT.md.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def slugify_agent_name(name: str) -> str:
    compact = _NON_SLUG.sub("-", name.strip().casefold()).strip("-")
    if len(compact) > 63:
        compact = compact[:63].rstrip("-")
    return compact if AGENT_ID.fullmatch(compact) else ""


def allocate_agent_identity(
    *,
    name: str | None,
    agent_id: str | None,
    taken: set[str],
) -> tuple[str, str]:
    if agent_id:
        _validate_id(agent_id)
        if agent_id in taken:
            raise FileExistsError(agent_id)
        display = name.strip() if name and name.strip() else agent_id
        if len(display) > 80:
            raise ValueError("agent name is too long")
        return agent_id, display
    if name and name.strip():
        display = name.strip()
        if len(display) > 80:
            raise ValueError("agent name is too long")
        base = slugify_agent_name(display) or _next_hames_id(taken)
        return _unique_agent_id(base, taken), display
    allocated = _next_hames_id(taken)
    return allocated, allocated


def _next_hames_id(taken: set[str]) -> str:
    index = 1
    while f"hames-{index}" in taken:
        index += 1
    return f"hames-{index}"


def _unique_agent_id(base: str, taken: set[str]) -> str:
    if base not in taken:
        return base
    suffix = 2
    while True:
        extra = f"-{suffix}"
        stem = base[: max(1, 63 - len(extra))].rstrip("-") or "hames"
        if AGENT_ID.fullmatch(stem) is None:
            stem = "hames"
        candidate = f"{stem}{extra}"
        if candidate not in taken:
            return candidate
        suffix += 1


def _split_agent_markdown(raw: str, *, origin: str = "AGENT.md") -> tuple[dict[str, object], str]:
    lines = raw.splitlines()
    if not lines or lines[0] != "---":
        raise ValueError(f"{origin}: AGENT.md must begin with YAML frontmatter")
    try:
        boundary = lines[1:].index("---") + 1
    except ValueError as exc:
        raise ValueError(f"{origin}: unterminated YAML frontmatter") from exc
    loaded: object = yaml.safe_load("\n".join(lines[1:boundary])) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{origin}: AGENT.md frontmatter must be a mapping")
    metadata = {str(key): value for key, value in cast(dict[object, object], loaded).items()}
    instructions = "\n".join(lines[boundary + 1 :]).strip()
    return metadata, instructions


def _validate_id(agent_id: str) -> None:
    if AGENT_ID.fullmatch(agent_id) is None:
        raise ValueError("agent ID must match [a-z][a-z0-9-]{0,62}")


def permitted_tools(capsule: AgentCapsule, available: set[str]) -> frozenset[str]:
    """Intersect a capsule's declared authority with the harness tool registry."""

    interaction_tools = {"ask_user"}.intersection(available)
    permitted = set(available)
    if capsule.metadata.authority == "read_only":
        permitted.intersection_update(READ_ONLY_TOOLS)
    if capsule.metadata.tools.allow:
        permitted.intersection_update(capsule.metadata.tools.allow)
    permitted.difference_update(capsule.metadata.tools.deny)
    permitted.update(interaction_tools.difference(capsule.metadata.tools.deny))
    return frozenset(permitted)


def direct_tools(capsule: AgentCapsule, allowed: frozenset[str]) -> frozenset[str]:
    """Limit a coordinator's own tools without stripping worker capabilities."""
    if capsule.metadata.delegation.coordinator_only:
        return allowed.intersection(READ_ONLY_TOOLS | {"task_update", "goal_report"})
    return allowed


class _SkillEntry(Protocol):
    slug: str
    score: float


def skill_permitted(capsule: AgentCapsule, slug: str) -> bool:
    policy = capsule.metadata.skills
    if slug in policy.deny:
        return False
    return not policy.allow or slug in policy.allow


def apply_agent_skill_policy[SkillEntry: _SkillEntry](
    capsule: AgentCapsule, items: list[SkillEntry], *, limit: int
) -> list[SkillEntry]:
    """Filter and pin catalog entries. Pin is catalog order, not a load grant."""

    allowed = [item for item in items if skill_permitted(capsule, item.slug)]
    by_slug = {item.slug: item for item in allowed}
    selected: list[SkillEntry] = []
    seen: set[str] = set()
    for slug in capsule.metadata.skills.pin:
        item = by_slug.get(slug)
        if item is None or slug in seen:
            continue
        selected.append(item)
        seen.add(slug)
        if len(selected) >= limit:
            return selected
    remaining = [item for item in allowed if item.slug not in seen]
    remaining.sort(key=lambda item: (-item.score, item.slug))
    selected.extend(remaining[: max(0, limit - len(selected))])
    return selected
