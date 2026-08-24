"""Strict core coding tools owned by the Python runtime."""

from __future__ import annotations

import asyncio
import difflib
import hashlib
import json
import os
import signal
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import ClassVar, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hames.blobs import BlobStore
from hames.config import ToolsConfig
from hames.providers import ToolDefinition
from hames.providers.base import JsonValue


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceArguments(ToolArguments):
    workspace: Literal["project", "scratch", "home"] = Field(
        default="project",
        description=(
            "Select project for the trusted working directory, scratch for disposable work, "
            "or home for a user-home path. Paths beginning with ~/ normalize to home."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def normalize_home_path(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        raw = dict(cast(dict[str, object], value))
        path_value = raw.get("path")
        if not isinstance(path_value, str):
            return raw
        home = Path.home().resolve(strict=False)
        if path_value == "~":
            raw["workspace"] = "home"
            raw["path"] = "."
            return raw
        if path_value.startswith("~/"):
            raw["workspace"] = "home"
            raw["path"] = path_value[2:]
            return raw
        candidate = Path(path_value)
        if candidate.is_absolute() and candidate.is_relative_to(home):
            raw["workspace"] = "home"
            relative = candidate.relative_to(home)
            raw["path"] = relative.as_posix() if relative.parts else "."
            return raw
        return raw


class ReadFileArguments(WorkspaceArguments):
    path: str
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def valid_range(self) -> ReadFileArguments:
        if self.start_line is not None and self.end_line is not None:
            if self.end_line < self.start_line:
                raise ValueError("end_line must not precede start_line")
        return self


class ListDirArguments(WorkspaceArguments):
    path: str = "."
    include_hidden: bool = False


class WriteFileArguments(WorkspaceArguments):
    path: str
    content: str
    create_parents: bool = False


class EditFileArguments(WorkspaceArguments):
    path: str
    old_text: str = Field(min_length=1)
    new_text: str


class ShellArguments(WorkspaceArguments):
    command: str = Field(min_length=1)
    timeout_seconds: float | None = Field(default=None, gt=0)


class SpawnAgentArguments(ToolArguments):
    agent_id: str
    task: str = Field(min_length=1)
    evidence_event_ids: list[str] = Field(default_factory=list, max_length=8)
    project_scope: Literal["current_workspace"] = "current_workspace"
    requested_result_format: Literal["summary", "markdown", "json"] = "summary"


class SkillLoadArguments(ToolArguments):
    id: str = Field(min_length=1)


class SkillAuthorArguments(ToolArguments):
    goal: str = Field(min_length=1, max_length=4000)
    scope: Literal["workspace", "agent"] = "workspace"
    target_skill_id: str | None = None


class SkillRunArguments(ToolArguments):
    id: str = Field(min_length=1)
    script: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list, max_length=64)


class MemorySearchArguments(ToolArguments):
    query: str = Field(default="", max_length=1000)
    status: Literal["proposed", "active", "rejected", "superseded", "retracted", "all"] = "active"
    layer: Literal["relationship", "semantic", "episodic"] | None = None
    limit: int = Field(default=20, ge=1, le=50)


class MemoryAddArguments(ToolArguments):
    layer: Literal["relationship", "semantic", "episodic"]
    visibility: Literal["global", "agent_private", "workspace", "session_team"] = "workspace"
    subject: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=120)
    value: JsonValue
    summary: str = Field(min_length=1, max_length=2000)
    confidence: float = Field(default=0.95, ge=0.6, le=1)
    importance: float = Field(default=0.9, ge=0.65, le=1)


class MemoryEditArguments(ToolArguments):
    memory_id: str = Field(min_length=1)
    layer: Literal["relationship", "semantic", "episodic"] | None = None
    visibility: Literal["global", "agent_private", "workspace", "session_team"] | None = None
    subject: str | None = Field(default=None, min_length=1, max_length=300)
    predicate: str | None = Field(default=None, min_length=1, max_length=120)
    value: JsonValue | None = None
    summary: str | None = Field(default=None, min_length=1, max_length=2000)
    confidence: float | None = Field(default=None, ge=0.6, le=1)
    importance: float | None = Field(default=None, ge=0.65, le=1)

    @model_validator(mode="after")
    def has_change(self) -> MemoryEditArguments:
        changed = self.model_fields_set - {"memory_id"}
        if not changed:
            raise ValueError("memory_edit requires at least one replacement field")
        return self


class MemoryForgetArguments(ToolArguments):
    memory_id: str = Field(min_length=1)
    reason: str = Field(default="explicit user request", min_length=1, max_length=1000)


class ScarListArguments(ToolArguments):
    status: (
        Literal[
            "candidate", "open", "repair_proposed", "guarded", "healed", "regressed", "dismissed"
        ]
        | None
    ) = None
    limit: int = Field(default=20, ge=1, le=50)


class ScarRecordArguments(ToolArguments):
    title: str = Field(min_length=1, max_length=300)
    severity: Literal["low", "medium", "high"] = "medium"
    failure_signature: str = Field(min_length=1, max_length=1000)
    description: str = Field(min_length=1, max_length=4000)
    expected_behavior: str = Field(min_length=1, max_length=4000)
    scope: Literal["global", "workspace", "agent"] = "workspace"


class ScarControlArguments(ToolArguments):
    scar_id: str = Field(min_length=1)
    action: Literal["open", "dismiss", "delete"]
    reason: str = Field(min_length=1, max_length=1000)


class SkillCatalogArguments(ToolArguments):
    query: str = Field(default="", max_length=1000)
    limit: int = Field(default=20, ge=1, le=50)


class SkillControlArguments(ToolArguments):
    id: str = Field(min_length=1, description="Visible Skill slug")
    action: Literal["pin", "unpin", "archive", "restore", "rollback"]
    reason: str = Field(min_length=1, max_length=1000)


class SessionTitleArguments(ToolArguments):
    title: str = Field(
        min_length=1,
        max_length=80,
        description="Concise human-readable title for this conversation",
    )


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["completed", "failed", "rejected"]
    summary: str
    content: str = ""
    structured_data: dict[str, JsonValue] = Field(default_factory=dict)
    truncated: bool = False
    blob_references: list[str] = Field(default_factory=list)
    duration_seconds: float = 0.0

    def for_model(self) -> str:
        return json.dumps(self.model_dump(mode="json"), separators=(",", ":"))


@dataclass(frozen=True, slots=True)
class ToolContext:
    project_root: Path
    scratch_root: Path
    blobs: BlobStore
    config: ToolsConfig

    def root_for(self, workspace: str) -> Path:
        if workspace == "project":
            return self.project_root
        if workspace == "home":
            return Path.home()
        self.scratch_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.scratch_root.chmod(0o700)
        return self.scratch_root

    def resolve(self, workspace: str, value: str, *, must_exist: bool) -> Path:
        root = self.root_for(workspace).resolve(strict=True)
        relative = PurePath(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("path must remain relative to the selected workspace")
        candidate = root.joinpath(*relative.parts)
        resolved = candidate.resolve(strict=must_exist)
        if not resolved.is_relative_to(root):
            raise ValueError("path escapes the selected workspace")
        return resolved


class ToolBase:
    name = ""
    description = ""
    side_effect_class = "read"
    arguments_type: ClassVar[type[ToolArguments]] = ToolArguments

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            input_schema=self.arguments_type.model_json_schema(),  # type: ignore[arg-type]
        )

    async def execute(self, context: ToolContext, arguments: ToolArguments) -> ToolResult:
        raise NotImplementedError


class ReadFileTool(ToolBase):
    name = "read_file"
    description = (
        "Read a UTF-8 text file from the project, confirmed user home, or disposable scratch "
        "workspace. Use workspace home or a ~/ path for files under the user home."
    )
    arguments_type: ClassVar[type[ToolArguments]] = ReadFileArguments

    async def execute(self, context: ToolContext, arguments: ToolArguments) -> ToolResult:
        started = time.monotonic()
        args = ReadFileArguments.model_validate(arguments)
        try:
            path = context.resolve(args.workspace, args.path, must_exist=True)
            if not path.is_file():
                raise ValueError("path is not a file")
            size = path.stat().st_size
            if size > context.config.read_byte_limit:
                raise ValueError(
                    f"file exceeds read limit of {context.config.read_byte_limit} bytes"
                )
            raw = await asyncio.to_thread(path.read_bytes)
            if b"\x00" in raw:
                raise ValueError("binary file cannot be read as text")
            text = raw.decode("utf-8")
            lines = text.splitlines(keepends=True)
            start = (args.start_line or 1) - 1
            end = args.end_line or len(lines)
            selected = "".join(lines[start:end])
            content, truncated, references = _bounded_content(selected, context)
            return ToolResult(
                status="completed",
                summary=f"read {args.path}",
                content=content,
                structured_data={"path": args.path, "bytes": size, "lines": len(lines)},
                truncated=truncated,
                blob_references=references,
                duration_seconds=time.monotonic() - started,
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return _failure(self.name, exc, started)


class ListDirTool(ToolBase):
    name = "list_dir"
    description = (
        "List typed entries in a project, confirmed user home, or disposable scratch directory."
    )
    arguments_type: ClassVar[type[ToolArguments]] = ListDirArguments

    async def execute(self, context: ToolContext, arguments: ToolArguments) -> ToolResult:
        started = time.monotonic()
        args = ListDirArguments.model_validate(arguments)
        try:
            path = context.resolve(args.workspace, args.path, must_exist=True)
            if not path.is_dir():
                raise ValueError("path is not a directory")
            children = await asyncio.to_thread(lambda: sorted(path.iterdir(), key=lambda p: p.name))
            if not args.include_hidden:
                children = [item for item in children if not item.name.startswith(".")]
            truncated = len(children) > context.config.list_entry_limit
            children = children[: context.config.list_entry_limit]
            entries: list[dict[str, JsonValue]] = []
            for item in children:
                kind = "symlink" if item.is_symlink() else "directory" if item.is_dir() else "file"
                entries.append({"name": item.name, "type": kind})
            return ToolResult(
                status="completed",
                summary=f"listed {args.path}",
                content="\n".join(f"{item['type']}\t{item['name']}" for item in entries),
                structured_data={"path": args.path, "entries": cast(list[JsonValue], entries)},
                truncated=truncated,
                duration_seconds=time.monotonic() - started,
            )
        except (OSError, ValueError) as exc:
            return _failure(self.name, exc, started)


class WriteFileTool(ToolBase):
    name = "write_file"
    description = "Atomically create or fully replace a UTF-8 text file."
    side_effect_class = "write"
    arguments_type: ClassVar[type[ToolArguments]] = WriteFileArguments

    async def execute(self, context: ToolContext, arguments: ToolArguments) -> ToolResult:
        started = time.monotonic()
        args = WriteFileArguments.model_validate(arguments)
        try:
            path = context.resolve(args.workspace, args.path, must_exist=False)
            if not path.parent.exists():
                if not args.create_parents:
                    raise ValueError("parent directory does not exist")
                path.parent.mkdir(mode=0o700, parents=True)
                context.resolve(args.workspace, args.path, must_exist=False)
            existed = path.exists()
            before = path.read_bytes() if existed else b""
            if existed and not path.is_file():
                raise ValueError("path is not a regular file")
            content = args.content.encode()
            await asyncio.to_thread(_atomic_write, path, content)
            return ToolResult(
                status="completed",
                summary=f"wrote {args.path}",
                structured_data={
                    "path": args.path,
                    "created": not existed,
                    "before_sha256": hashlib.sha256(before).hexdigest() if existed else None,
                    "after_sha256": hashlib.sha256(content).hexdigest(),
                    "bytes": len(content),
                },
                duration_seconds=time.monotonic() - started,
            )
        except (OSError, ValueError) as exc:
            return _failure(self.name, exc, started)


class EditFileTool(ToolBase):
    name = "edit_file"
    description = "Atomically replace exactly one literal text occurrence in a UTF-8 file."
    side_effect_class = "write"
    arguments_type: ClassVar[type[ToolArguments]] = EditFileArguments

    async def execute(self, context: ToolContext, arguments: ToolArguments) -> ToolResult:
        started = time.monotonic()
        args = EditFileArguments.model_validate(arguments)
        try:
            path = context.resolve(args.workspace, args.path, must_exist=True)
            before = await asyncio.to_thread(path.read_text, encoding="utf-8")
            count = before.count(args.old_text)
            if count != 1:
                raise ValueError(f"expected exactly one match; found {count}")
            after = before.replace(args.old_text, args.new_text, 1)
            await asyncio.to_thread(_atomic_write, path, after.encode())
            diff = "".join(
                difflib.unified_diff(
                    before.splitlines(keepends=True),
                    after.splitlines(keepends=True),
                    fromfile=f"a/{args.path}",
                    tofile=f"b/{args.path}",
                )
            )
            content, truncated, references = _bounded_content(diff, context)
            return ToolResult(
                status="completed",
                summary=f"edited {args.path}",
                content=content,
                structured_data={
                    "path": args.path,
                    "before_sha256": hashlib.sha256(before.encode()).hexdigest(),
                    "after_sha256": hashlib.sha256(after.encode()).hexdigest(),
                },
                truncated=truncated,
                blob_references=references,
                duration_seconds=time.monotonic() - started,
            )
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            return _failure(self.name, exc, started)


class ShellTool(ToolBase):
    name = "shell"
    description = "Run a Bash command in the project or disposable scratch workspace."
    side_effect_class = "shell"
    arguments_type: ClassVar[type[ToolArguments]] = ShellArguments

    async def execute(self, context: ToolContext, arguments: ToolArguments) -> ToolResult:
        started = time.monotonic()
        args = ShellArguments.model_validate(arguments)
        timeout = args.timeout_seconds or context.config.shell_timeout_seconds
        if timeout > context.config.shell_max_timeout_seconds:
            return _failure(
                self.name,
                ValueError(f"timeout exceeds {context.config.shell_max_timeout_seconds} seconds"),
                started,
            )
        cwd = context.root_for(args.workspace).resolve(strict=True)
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-lc",
                args.command,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=_shell_environment(),
            )
            if process.stdout is None or process.stderr is None:  # pragma: no cover
                raise RuntimeError("shell pipes were not created")
            stdout_task = asyncio.create_task(
                _capture_stream(process.stdout, context.config.capture_byte_limit)
            )
            stderr_task = asyncio.create_task(
                _capture_stream(process.stderr, context.config.capture_byte_limit)
            )
            try:
                async with asyncio.timeout(timeout):
                    exit_code = await process.wait()
            except TimeoutError:
                _kill_process_group(process)
                await process.wait()
                await asyncio.gather(stdout_task, stderr_task)
                raise TimeoutError(f"shell command timed out after {timeout:g} seconds") from None
            stdout, stdout_truncated = await stdout_task
            stderr, stderr_truncated = await stderr_task
            stdout_text = stdout.decode("utf-8", errors="replace")
            stderr_text = stderr.decode("utf-8", errors="replace")
            full = f"stdout:\n{stdout_text}\nstderr:\n{stderr_text}"
            content, model_truncated, references = _bounded_content(full, context)
            captured_truncated = stdout_truncated or stderr_truncated
            status: Literal["completed", "failed"] = "completed" if exit_code == 0 else "failed"
            return ToolResult(
                status=status,
                summary=f"shell exited with code {exit_code}",
                content=content,
                structured_data={
                    "command": args.command,
                    "workspace": args.workspace,
                    "exit_code": exit_code,
                    "stdout": stdout_text[: context.config.model_result_char_limit],
                    "stderr": stderr_text[: context.config.model_result_char_limit],
                },
                truncated=model_truncated or captured_truncated,
                blob_references=references,
                duration_seconds=time.monotonic() - started,
            )
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                _kill_process_group(process)
                await process.wait()
            raise
        except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
            return _failure(self.name, exc, started)


class SpawnAgentTool(ToolBase):
    name = "spawn_agent"
    description = "Delegate a bounded task to an allowed child agent with explicit evidence."
    side_effect_class = "delegation"
    arguments_type: ClassVar[type[ToolArguments]] = SpawnAgentArguments


class SkillLoadTool(ToolBase):
    name = "skill_load"
    description = "Load one active Skill by ID into this run before following its procedure."
    arguments_type: ClassVar[type[ToolArguments]] = SkillLoadArguments


class SkillAuthorTool(ToolBase):
    name = "skill_author"
    description = (
        "Request autonomous creation or correction of a reusable Skill after this run settles."
    )
    side_effect_class = "skill_authoring"
    arguments_type: ClassVar[type[ToolArguments]] = SkillAuthorArguments


class SkillRunTool(ToolBase):
    name = "skill_run"
    description = "Run a declared script from a Skill already loaded into this run."
    side_effect_class = "skill_script"
    arguments_type: ClassVar[type[ToolArguments]] = SkillRunArguments


class MemorySearchTool(ToolBase):
    name = "memory_search"
    description = (
        "Search memories visible to this session, including their IDs and lifecycle state."
    )
    arguments_type: ClassVar[type[ToolArguments]] = MemorySearchArguments


class MemoryAddTool(ToolBase):
    name = "memory_add"
    description = (
        "Add an explicit, durable memory requested by the user. Never store credentials or secrets."
    )
    side_effect_class = "memory_write"
    arguments_type: ClassVar[type[ToolArguments]] = MemoryAddArguments


class MemoryEditTool(ToolBase):
    name = "memory_edit"
    description = (
        "Correct a visible active memory by creating a new immutable record that supersedes it."
    )
    side_effect_class = "memory_write"
    arguments_type: ClassVar[type[ToolArguments]] = MemoryEditArguments


class MemoryForgetTool(ToolBase):
    name = "memory_forget"
    description = "Retract a visible active memory while preserving its auditable history."
    side_effect_class = "memory_delete"
    arguments_type: ClassVar[type[ToolArguments]] = MemoryForgetArguments


class ScarListTool(ToolBase):
    name = "scar_list"
    description = "List visible behavioral scars and their lifecycle state."
    arguments_type: ClassVar[type[ToolArguments]] = ScarListArguments


class ScarRecordTool(ToolBase):
    name = "scar_record"
    description = (
        "Record and open a durable behavioral scar from an explicit user correction or failure."
    )
    side_effect_class = "scar_write"
    arguments_type: ClassVar[type[ToolArguments]] = ScarRecordArguments


class ScarControlTool(ToolBase):
    name = "scar_control"
    description = "Open a candidate scar or dismiss a visible scar with an explicit reason."
    side_effect_class = "scar_write"
    arguments_type: ClassVar[type[ToolArguments]] = ScarControlArguments


class SkillCatalogTool(ToolBase):
    name = "skill_catalog"
    description = (
        "Search active Skills visible to this session and inspect their lifecycle metadata."
    )
    arguments_type: ClassVar[type[ToolArguments]] = SkillCatalogArguments


class SkillControlTool(ToolBase):
    name = "skill_control"
    description = "Pin, unpin, archive, restore, or roll back a visible Skill."
    side_effect_class = "skill_control"
    arguments_type: ClassVar[type[ToolArguments]] = SkillControlArguments


class SessionTitleTool(ToolBase):
    name = "session_title_set"
    description = (
        "Set a concise session title that summarizes the conversation. Use it early and update "
        "it only when the conversation's purpose materially changes."
    )
    side_effect_class = "session_metadata"
    arguments_type: ClassVar[type[ToolArguments]] = SessionTitleArguments


class ToolRegistry:
    def __init__(self) -> None:
        values: list[ToolBase] = [
            ReadFileTool(),
            ListDirTool(),
            WriteFileTool(),
            EditFileTool(),
            ShellTool(),
            SpawnAgentTool(),
            SkillLoadTool(),
            SkillAuthorTool(),
            SkillRunTool(),
            MemorySearchTool(),
            MemoryAddTool(),
            MemoryEditTool(),
            MemoryForgetTool(),
            ScarListTool(),
            ScarRecordTool(),
            ScarControlTool(),
            SkillCatalogTool(),
            SkillControlTool(),
            SessionTitleTool(),
        ]
        self._tools = {tool.name: tool for tool in values}

    def definitions(self, allowed: frozenset[str] | None = None) -> list[ToolDefinition]:
        return [
            tool.definition()
            for tool in self._tools.values()
            if allowed is None or tool.name in allowed
        ]

    def get(self, name: str) -> ToolBase | None:
        return self._tools.get(name)

    def names(self) -> set[str]:
        return set(self._tools)

    def validate(self, name: str, arguments: dict[str, JsonValue]) -> ToolArguments:
        tool = self.get(name)
        if tool is None:
            raise ValueError(f"unknown tool: {name}")
        try:
            return tool.arguments_type.model_validate(arguments)
        except ValidationError as exc:
            raise ValueError(f"invalid {name} arguments: {exc}") from exc


async def _capture_stream(stream: asyncio.StreamReader, limit: int) -> tuple[bytes, bool]:
    chunks: list[bytes] = []
    retained = 0
    truncated = False
    while chunk := await stream.read(65_536):
        remaining = max(0, limit - retained)
        if remaining:
            selected = chunk[:remaining]
            chunks.append(selected)
            retained += len(selected)
        if len(chunk) > remaining:
            truncated = True
    return b"".join(chunks), truncated


def _kill_process_group(process: asyncio.subprocess.Process) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _shell_environment() -> dict[str, str]:
    secret_markers = (
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "PASSWD",
        "API_KEY",
        "APIKEY",
        "AUTH",
        "CREDENTIAL",
        "COOKIE",
    )
    return {
        key: value
        for key, value in os.environ.items()
        if not any(marker in key.upper() for marker in secret_markers)
    }


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=".hames-write-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _bounded_content(content: str, context: ToolContext) -> tuple[str, bool, list[str]]:
    if len(content) <= context.config.model_result_char_limit:
        return content, False, []
    digest = context.blobs.put(content.encode())
    limit = context.config.model_result_char_limit
    return content[:limit] + "\n[output truncated]", True, [digest]


def _failure(name: str, error: Exception, started: float) -> ToolResult:
    return ToolResult(
        status="failed",
        summary=f"{name} failed: {error}",
        structured_data={"error": type(error).__name__, "message": str(error)},
        duration_seconds=time.monotonic() - started,
    )
