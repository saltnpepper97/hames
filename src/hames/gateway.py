"""Versioned local HTTP/SSE gateway."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import shutil
import sqlite3
import time
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, cast
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hames import PROTOCOL_VERSION, __version__
from hames.agent import (
    AgentAvatar,
    AgentCapsule,
    AgentExecution,
    AgentRegistry,
    AgentSkills,
    AgentSummary,
    AgentTools,
    apply_agent_skill_policy,
    skill_permitted,
)
from hames.agent_execution import resolve_agent_execution
from hames.attachments import AttachmentUpload
from hames.blobs import BlobIntegrityError
from hames.broker import EventBroker
from hames.config import HamesConfig, ProviderProfileConfig, load_config
from hames.control import ControlStore
from hames.database import Database
from hames.environment import RuntimeEnvironmentSnapshot
from hames.evolution import Scar, ScarScope, ScarSeverity, ScarStatus, ScarStore
from hames.evolution_runtime import EvolutionManager
from hames.goals import Goal
from hames.inspection import (
    AgentUsageProjection,
    ContextInspection,
    RunInspection,
    RunSummary,
    ScarInspection,
    UsageProjection,
    agent_usage,
    export_transcript,
    inspect_context,
    inspect_run,
    inspect_scar,
    pooled_usage,
    session_runs,
    session_usage,
    workspace_daily_usage,
)
from hames.ledger import Event, EventIntegrityError, IntegrityResult, Ledger, Session
from hames.mcp_runtime import McpManager, McpServerSpec, McpServerView
from hames.memory import (
    MemoryCandidate,
    MemoryJob,
    MemoryLayer,
    MemoryRecord,
    MemoryStatus,
    MemoryVisibility,
    contains_secret,
)
from hames.memory_runtime import MemoryManager
from hames.message_queue import (
    QueuedMessage,
    QueueFullError,
    QueueState,
    SubmissionIdReuseError,
)
from hames.paths import HamesPaths
from hames.plans import PlanState
from hames.plugin_protocol import PluginProtocolError
from hames.plugin_runtime import (
    PluginInspectView,
    PluginManager,
    PluginProposalView,
    PluginView,
)
from hames.plugin_sandbox import PluginSandboxError
from hames.providers import Provider, ProviderError, ProviderModel
from hames.providers.registry import configured_providers
from hames.providers.scheduled import ScheduledProvider
from hames.rules import (
    ContextRule,
    ContextRuleCondition,
    PolicyRule,
)
from hames.runtime import RunManager
from hames.search_runtime import SearchMcpManager, SearchRuntimeStatus
from hames.skill_runtime import SkillManager
from hames.skills import SkillJob, SkillSummary, SkillVersion
from hames.tasks import SessionTaskList, TaskStatus
from hames.web_ui import WebUi, WebUiError, install_web_routes, web_error_response
from hames.workspaces import (
    DirectoryListing,
    NativeDirectoryPickerUnavailable,
    Workspace,
    WorkspaceRegistry,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSessionRequest(ApiModel):
    working_directory: str
    agent_id: str = ""
    provider: str = ""
    model: str = ""
    reasoning_effort: str = ""
    title: str | None = None
    inherit_session_id: str | None = None


class WorkspaceCreateRequest(ApiModel):
    path: str = Field(min_length=1, max_length=4096)
    title: str | None = Field(default=None, max_length=160)


class WorkspaceRenameRequest(ApiModel):
    title: str = Field(min_length=1, max_length=160)


class DirectoryCreateRequest(ApiModel):
    parent: str = Field(min_length=1, max_length=4096)
    name: str = Field(min_length=1, max_length=255)


class DirectoryPickerRequest(ApiModel):
    initial_path: str | None = Field(default=None, max_length=4096)


class PasteSpan(ApiModel):
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)
    line_count: int = Field(ge=1)
    byte_count: int = Field(ge=1)


def _empty_paste_spans() -> list[PasteSpan]:
    return []


class QueuedMessageEditRequest(ApiModel):
    content: str = Field(max_length=500_000)
    expected_content: str = Field(max_length=500_000)


class MessageRequest(ApiModel):
    submission_id: UUID = Field(default_factory=uuid4)
    content: str = Field(default="", max_length=500_000)
    remember: bool = False
    send_now: bool = False
    purpose: Literal["turn", "plan_note", "heal"] = "turn"
    paste_spans: list[PasteSpan] = Field(default_factory=_empty_paste_spans, max_length=64)
    attachments: list[AttachmentUpload] = Field(
        default_factory=lambda: list[AttachmentUpload](), max_length=8
    )

    @model_validator(mode="after")
    def validate_paste_spans(self) -> MessageRequest:
        if not self.content.strip() and not self.attachments:
            raise ValueError("message content or an attachment is required")
        encoded = self.content.encode()
        previous_end = 0
        for span in self.paste_spans:
            if span.start_byte < previous_end:
                raise ValueError("paste spans must be sorted and non-overlapping")
            if span.start_byte >= span.end_byte or span.end_byte > len(encoded):
                raise ValueError("paste span is outside message content")
            value = encoded[span.start_byte : span.end_byte]
            try:
                value.decode()
            except UnicodeDecodeError as exc:
                raise ValueError("paste span must align to UTF-8 boundaries") from exc
            if span.byte_count != len(value):
                raise ValueError("paste span byte_count does not match content")
            if span.line_count != value.count(b"\n") + 1:
                raise ValueError("paste span line_count does not match content")
            previous_end = span.end_byte
        return self


class UpdateSessionRequest(ApiModel):
    provider: str
    model: str
    reasoning_effort: str = ""


class UpdateSessionAgentRequest(ApiModel):
    agent_id: str


class UpdateSessionModeRequest(ApiModel):
    mode: Literal["manual", "auto", "plan"]


class UpdateSessionTitleRequest(ApiModel):
    title: str = Field(min_length=1, max_length=80)


class UpdateSessionPinnedRequest(ApiModel):
    pinned: bool


class MessageAccepted(ApiModel):
    submission_id: str
    replayed: bool = False
    disposition: Literal["started", "queued"]
    run_id: str | None = None
    queued: QueuedMessage | None = None


class CompactionAccepted(ApiModel):
    run_id: str
    trigger: Literal["manual"] = "manual"


class PlanExecuteRequest(ApiModel):
    agent_id: str | None = None
    strategy: Literal["keep", "compact"]
    note: str = Field(default="", max_length=8000)


class PlanExecutionAccepted(ApiModel):
    plan: PlanState
    tasks: SessionTaskList
    run_id: str


class TaskCreateRequest(ApiModel):
    text: str = Field(min_length=1, max_length=500)


class TaskUpdateRequest(ApiModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    status: TaskStatus | None = None
    position: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def has_change(self) -> TaskUpdateRequest:
        if self.text is None and self.status is None and self.position is None:
            raise ValueError("task update requires text, status, or position")
        return self


class GoalCreateRequest(ApiModel):
    objective: str = Field(min_length=1, max_length=8000)


class TrustStatus(ApiModel):
    path: str
    trusted: bool
    grant_id: str | None = None
    created_at: str | None = None


class ApprovalDecisionRequest(ApiModel):
    decision: Literal["approved", "approved_session", "denied"]
    request_hash: str = Field(min_length=64, max_length=64)


class ApprovalResolution(ApiModel):
    approval_id: str
    request_hash: str
    status: str
    approval_scope: str


class QuestionAnswerRequest(ApiModel):
    selected_option: str | None = Field(default=None, min_length=1, max_length=160)
    selected_options: list[str] = Field(default_factory=list, max_length=8)
    note: str = Field(default="", max_length=4000)
    custom_answer: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def valid_answer(self) -> QuestionAnswerRequest:
        modes = sum(
            (
                self.selected_option is not None,
                bool(self.selected_options),
                bool(self.custom_answer.strip()),
            )
        )
        if modes != 1:
            raise ValueError("provide selected_option, selected_options, or custom_answer")
        if len({option.strip().casefold() for option in self.selected_options}) != len(
            self.selected_options
        ):
            raise ValueError("selected_options must be unique")
        if self.custom_answer.strip() and self.note.strip():
            raise ValueError("a custom answer cannot also have an option note")
        return self


class QuestionResolution(ApiModel):
    question_id: str
    answer: str
    answer_type: Literal["single_choice", "multiple_choice", "text"]
    selected_option: str | None
    selected_description: str
    selected_options: list[str]
    selected_descriptions: list[str]
    note: str
    custom: bool


class ForkSessionRequest(ApiModel):
    at: str | None = None
    title: str | None = None
    agent_id: str | None = None


class AgentCreateRequest(ApiModel):
    name: str | None = Field(default=None, max_length=80)
    authority: Literal["standard", "read_only"] = "standard"
    source: str | None = Field(default=None, max_length=65_536)


class AgentUpdateRequest(ApiModel):
    default_model: AgentExecution | None = None
    name: str | None = Field(default=None, min_length=1, max_length=80)
    instructions: str | None = Field(default=None, min_length=1, max_length=65_536)
    source: str | None = Field(default=None, min_length=1, max_length=65_536)
    tools: AgentTools | None = None
    skills: AgentSkills | None = None
    avatar: AgentAvatar | None = None

    @model_validator(mode="after")
    def has_one_update_form(self) -> AgentUpdateRequest:
        structured = "default_model" in self.model_fields_set or any(
            value is not None
            for value in (self.name, self.instructions, self.tools, self.skills, self.avatar)
        )
        if self.source is not None and structured:
            raise ValueError("source cannot be combined with structured agent updates")
        if self.source is None and not structured:
            raise ValueError("agent update requires source or a structured field")
        return self


class MemoryCaptureRequest(ApiModel):
    content: str = Field(min_length=1, max_length=32_000)


class MemoryCreateRequest(ApiModel):
    layer: MemoryLayer
    visibility: MemoryVisibility
    subject: str = Field(min_length=1, max_length=300)
    predicate: str = Field(min_length=1, max_length=120)
    value: str = Field(min_length=1, max_length=32_000)
    summary: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def normalize_fields(self) -> MemoryCreateRequest:
        for name in ("subject", "predicate", "value", "summary"):
            current = getattr(self, name)
            normalized = current.strip() if name == "value" else " ".join(current.split())
            if not normalized:
                raise ValueError(f"{name} must not be blank")
            setattr(self, name, normalized)
        return self


class MemoryTransitionRequest(ApiModel):
    action: Literal["accept", "reject", "retract"]
    reason: str = Field(default="user_request", min_length=1, max_length=240)


class MemoryPromotionRequest(ApiModel):
    visibility: MemoryVisibility


class MemoryDeleteResponse(ApiModel):
    memory_id: str
    deleted: bool


class ScarDeleteResponse(ApiModel):
    scar_id: str
    deleted: bool


class ScarCreateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=300)
    severity: ScarSeverity = "medium"
    scope: ScarScope = "workspace"
    failure_signature: str = Field(min_length=1, max_length=1000)
    description: str = Field(min_length=1, max_length=4000)
    expected_behavior: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def normalize_fields(self) -> ScarCreateRequest:
        self.title = " ".join(self.title.split())
        self.failure_signature = " ".join(self.failure_signature.split())
        self.description = self.description.strip()
        self.expected_behavior = self.expected_behavior.strip()
        for name in ("title", "failure_signature", "description", "expected_behavior"):
            if not getattr(self, name):
                raise ValueError(f"{name} must not be blank")
        return self


class SkillDeleteResponse(ApiModel):
    skill_id: str
    slug: str
    deleted: bool


class SkillAuthorRequest(ApiModel):
    goal: str = Field(min_length=1, max_length=4000)
    scope: Literal["workspace", "agent"] = "workspace"
    target_skill_id: str | None = None


class PluginPathRequest(ApiModel):
    path: str = Field(min_length=1, max_length=1024)


class PluginUploadFile(ApiModel):
    path: str = Field(min_length=1, max_length=512)
    data_base64: str = Field(max_length=20_000_000)


class PluginUploadRequest(ApiModel):
    files: list[PluginUploadFile] = Field(min_length=1, max_length=256)


class PluginUploadInspection(ApiModel):
    upload_id: str
    plugin: PluginInspectView


class SkillControlRequest(ApiModel):
    reason: str = Field(default="user_override", min_length=1, max_length=240)


class CorrectionRequest(ApiModel):
    content: str = Field(min_length=1, max_length=8000)
    target_event_id: str | None = None


class ScarUpdateRequest(ApiModel):
    title: str = Field(min_length=1, max_length=300)
    severity: Literal["low", "medium", "high"]
    description: str = Field(min_length=1, max_length=4000)
    expected_behavior: str = Field(min_length=1, max_length=4000)


class ContextRuleRequest(ApiModel):
    description: str = Field(min_length=1, max_length=2000)
    require_source_types: list[str] = Field(min_length=1, max_length=16)
    workspace_paths: list[str] = Field(default_factory=list, max_length=16)
    agent_ids: list[str] = Field(default_factory=list, max_length=16)
    scar_id: str | None = None


class PolicyRuleRequest(ApiModel):
    action: Literal["deny", "confirm"]
    pattern: str = Field(min_length=1, max_length=2000)
    reason: str = Field(min_length=1, max_length=2000)
    scar_id: str | None = None


class RuleDecisionRequest(ApiModel):
    reason: str = Field(default="user_decision", min_length=1, max_length=240)


_RULE_STATUSES = {"proposed", "active", "retired"}


def _rule_status_filter(status: str | None) -> Literal["proposed", "active", "retired"] | None:
    if status is None:
        return None
    if status not in _RULE_STATUSES:
        raise ApiError(422, "invalid_status_filter", f"unknown rule status: {status}")
    return cast(Literal["proposed", "active", "retired"], status)


def _rule_action(action: str, kind: str) -> Literal["activate", "retire"]:
    if action not in {"activate", "retire"}:
        raise ApiError(404, "unknown_action", f"unknown {kind} action: {action}")
    return cast(Literal["activate", "retire"], action)


class AgentPublic(ApiModel):
    slug: str = ""
    id: str
    name: str
    authority: str
    path: str
    content_hash: str
    avatar: AgentAvatar | None


class AgentDetail(AgentPublic):
    default_model: AgentExecution | None = None
    source: str
    instructions: str
    tools_allow: list[str]
    tools_deny: list[str]
    skills_allow: list[str]
    skills_deny: list[str]
    skills_pin: list[str]
    delegation_allowed: bool
    delegation_targets: list[str]
    deprecated_fields: list[str]


class AgentCapabilities(ApiModel):
    tools: list[str]
    skills: list[SkillSummary]


class ProviderProfile(ApiModel):
    id: str
    adapter: str
    endpoint: str
    configured_model: str
    default_reasoning_effort: str
    supported_reasoning_efforts: list[str]


class ProviderProbeError(ApiModel):
    code: str
    message: str
    retryable: bool
    details: dict[str, object] = Field(default_factory=dict)


class ProviderProbe(ApiModel):
    id: str
    adapter: str
    reachable: bool
    models: list[ProviderModel]
    error: ProviderProbeError | None = None


class Health(ApiModel):
    status: str
    version: str
    protocol_version: int
    database_ready: bool
    provider_profiles: list[str]
    default_provider: str
    active_runs: int
    active_terminals: int
    search: SearchRuntimeStatus
    mcp_servers: int
    mcp_ready: int
    mcp_degraded: int


class BackgroundTerminal(ApiModel):
    id: str
    session_id: str
    command: str
    workspace: Literal["project", "home"]
    pid: int
    status: Literal["running", "stopping"]
    started_at: str
    timeout_seconds: float | None = None


class BackgroundTerminalsStopped(ApiModel):
    closed: int


class ToolResultDetails(ApiModel):
    event_id: str
    tool_call_id: str
    content: str
    total_chars: int
    returned_chars: int
    total_lines: int
    returned_lines: int
    complete: bool
    retained: bool


TOOL_RESULT_DETAIL_BYTE_LIMIT = 1_048_576


def _bounded_tool_result_details(
    event: Event, content: str, *, retained: bool
) -> ToolResultDetails:
    encoded = content.encode()
    returned = encoded[:TOOL_RESULT_DETAIL_BYTE_LIMIT]
    while returned:
        try:
            display = returned.decode()
            break
        except UnicodeDecodeError:
            returned = returned[:-1]
    else:
        display = ""
    return ToolResultDetails(
        event_id=event.id,
        tool_call_id=str(event.payload.get("tool_call_id", "")),
        content=display,
        total_chars=len(content),
        returned_chars=len(display),
        total_lines=len(content.splitlines()),
        returned_lines=len(display.splitlines()),
        complete=len(returned) == len(encoded),
        retained=retained,
    )


class ApiError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.retryable = retryable
        self.details = details or {}


async def _attach_codex_account_usage(
    usage: UsageProjection,
    provider: Provider | None,
) -> None:
    if provider is None or provider.adapter != "codex":
        return
    cached = getattr(provider, "cached_account_rate_limits", lambda: None)()
    reader = getattr(provider, "account_rate_limits", None)
    try:
        if reader is not None:
            usage.account_rate_limits = await asyncio.wait_for(reader(), timeout=1.5)
    except TimeoutError:
        if cached is not None:
            usage.account_rate_limits = cached
        else:
            usage.account_rate_limits_error = "Codex account usage unavailable: timed out"
    # Optional account metrics must never make locally recorded token totals unavailable.
    except Exception as exc:
        if cached is not None:
            usage.account_rate_limits = cached
        else:
            usage.account_rate_limits_error = f"Codex account usage unavailable: {exc}"


async def _attach_grok_account_usage(usage: UsageProjection, provider: Provider | None) -> None:
    if provider is None:
        return
    usage.grok_account_configured = True
    reader = getattr(provider, "account_rate_limits", None)
    try:
        if reader is None:
            raise ValueError("Account limits unavailable")
        usage.grok_account_usage = await asyncio.wait_for(reader(), timeout=5.0)
    except Exception:
        # Never expose upstream billing payloads or block locally recorded totals.
        usage.grok_account_usage = getattr(provider, "cached_account_rate_limits", lambda: None)()
        usage.grok_account_usage_error = (
            "Showing last known Grok usage; refresh failed."
            if usage.grok_account_usage is not None
            else "Grok account usage is unavailable. Try refreshing."
        )


@dataclass(slots=True)
class GatewayState:
    paths: HamesPaths
    config: HamesConfig
    ledger: Ledger
    workspaces: WorkspaceRegistry
    controls: ControlStore
    providers: dict[str, Provider]
    broker: EventBroker
    runs: RunManager
    memory: MemoryManager
    skills: SkillManager
    evolution: EvolutionManager
    plugins: PluginManager
    search: SearchMcpManager
    mcp: McpManager
    web: WebUi
    token: str

    @classmethod
    def create(
        cls,
        paths: HamesPaths,
        *,
        providers: dict[str, Provider] | None = None,
    ) -> GatewayState:
        paths.ensure_foundation()
        config = load_config(paths)
        database = Database(paths.database)
        database.migrate()
        ledger = Ledger(database, blob_threshold_bytes=config.ledger.blob_threshold_bytes)
        workspaces = WorkspaceRegistry(database)
        controls = ControlStore(database)
        broker = EventBroker()
        last_mcp_notice: dict[tuple[str, str, str | None], float] = {}

        async def publish_mcp_notice(
            server_id: str,
            code: str,
            message: str,
            error: bool,
            session_id: str | None,
        ) -> None:
            normalized = " ".join(message.split())[:500]
            key = (server_id, code, session_id)
            now = time.monotonic()
            if code in {"mcp.tool.progress", "mcp.server.log"}:
                previous = last_mcp_notice.get(key, 0.0)
                if now - previous < 0.25:
                    return
                last_mcp_notice[key] = now
            envelope: dict[str, object] = {
                "durable": False,
                "run_id": f"mcp:{server_id}",
                "event_type": "runtime.notice",
                "payload": {
                    "code": code,
                    "message": normalized,
                    "details": {"server_id": server_id, "error": error},
                },
            }
            if session_id is None:
                await broker.publish_all(envelope)
            else:
                await broker.publish(session_id, envelope)

        mcp = McpManager(database, publish_mcp_notice)
        raw_providers = providers or configured_providers(config)
        selected_providers: dict[str, Provider] = {
            profile_id: ScheduledProvider(provider)
            for profile_id, provider in raw_providers.items()
        }
        search = SearchMcpManager(paths, config)
        runs = RunManager(
            ledger=ledger,
            paths=paths,
            config=config,
            controls=controls,
            providers=selected_providers,
            broker=broker,
            search=search,
            mcp=mcp,
        )
        memory = MemoryManager(
            ledger=ledger,
            config=config,
            providers=selected_providers,
            broker=broker,
        )
        skills = SkillManager(
            ledger=ledger,
            config=config,
            providers=selected_providers,
            broker=broker,
            registry=runs.skills,
        )
        plugins = PluginManager(
            paths=paths,
            ledger=ledger,
            config=config,
            events=broker,
            policy=runs.policy,
        )
        evolution = EvolutionManager(
            ledger=ledger,
            config=config,
            broker=broker,
            store=ScarStore(ledger),
            skills=runs.skills,
            memory=runs.memory,
            providers=selected_providers,
            skill_manager=skills,
            plugin_manager=plugins,
        )
        runs.attach_memory_manager(memory)
        runs.attach_skill_manager(skills)
        runs.attach_evolution_manager(evolution)
        runs.attach_plugin_manager(plugins)

        return cls(
            paths,
            config,
            ledger,
            workspaces,
            controls,
            selected_providers,
            broker,
            runs,
            memory,
            skills,
            evolution,
            plugins,
            search,
            mcp,
            WebUi(config.gateway),
            paths.read_gateway_token(),
        )

    def provider_profile(self, profile_id: str) -> ProviderProfileConfig | None:
        return self.config.providers.get(profile_id)

    @property
    def agents(self) -> AgentRegistry:
        return AgentRegistry(self.paths.agents)


def create_app(state: GatewayState) -> FastAPI:

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        await state.search.start()
        await state.mcp.start_enabled()
        await state.plugins.start_enabled()
        await state.runs.recover_approvals()
        await state.runs.recover_queues()
        await state.runs.recover_goals()
        try:
            yield
        finally:
            await state.runs.close()

    app = FastAPI(title="Hames Gateway", version=__version__, lifespan=lifespan)

    async def authenticate(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if authorization == f"Bearer {state.token}":
            return
        state.web.authorize(request)

    async def authenticate_bearer(
        authorization: Annotated[str | None, Header()] = None,
    ) -> None:
        if authorization != f"Bearer {state.token}":
            raise ApiError(401, "unauthorized", "a valid local gateway token is required")

    auth = [Depends(authenticate)]
    bearer_auth = [Depends(authenticate_bearer)]

    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": exc.retryable,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = cast(list[dict[str, object]], json.loads(json.dumps(exc.errors(), default=str)))
        first = errors[0] if errors else {}
        raw_location = first.get("loc", [])
        location_parts = cast(list[object], raw_location) if isinstance(raw_location, list) else []
        location = ".".join(str(part) for part in location_parts if part != "body")
        detail = str(first.get("msg", "request validation failed"))
        message = f"{location}: {detail}" if location else detail
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": message,
                    "retryable": False,
                    "details": {"errors": errors},
                }
            },
        )

    @app.exception_handler(WebUiError)
    async def web_ui_error_handler(_: Request, exc: WebUiError) -> Response:
        return web_error_response(exc)

    @app.exception_handler(EventIntegrityError)
    async def integrity_error_handler(_: Request, exc: EventIntegrityError) -> JSONResponse:
        return JSONResponse(
            status_code=409,
            content={
                "error": {
                    "code": "integrity_error",
                    "message": str(exc),
                    "retryable": False,
                    "details": {},
                }
            },
        )

    @app.get("/v1/health", response_model=Health)
    async def health() -> Health:
        mcp_servers = state.mcp.list()
        return Health(
            status="ok",
            version=__version__,
            protocol_version=PROTOCOL_VERSION,
            database_ready=state.paths.database.exists(),
            provider_profiles=sorted(state.providers),
            default_provider=state.config.runtime.default_provider,
            active_runs=state.runs.active_run_count,
            active_terminals=state.runs.active_background_terminal_count,
            search=state.search.status(),
            mcp_servers=len(mcp_servers),
            mcp_ready=sum(server.status == "ready" for server in mcp_servers),
            mcp_degraded=sum(server.status == "degraded" for server in mcp_servers),
        )

    from hames.connections import install_connections

    install_connections(app, state, auth)

    @app.get("/v1/providers", dependencies=auth, response_model=list[ProviderProfile])
    async def providers_endpoint() -> list[ProviderProfile]:
        return [
            _public_profile(state, profile_id, provider)
            for profile_id, provider in sorted(state.providers.items())
        ]

    @app.post(
        "/v1/providers/{profile_id}/probe",
        dependencies=auth,
        response_model=ProviderProbe,
    )
    async def probe_provider(profile_id: str) -> ProviderProbe:
        provider = state.providers.get(profile_id)
        if provider is None:
            raise ApiError(404, "unknown_provider", f"unknown provider: {profile_id}")
        return await _probe(profile_id, provider)

    async def resolve_selection(
        profile_id: str,
        requested_model: str,
        requested_effort: str,
        *,
        default_model: str = "",
        default_effort: str = "",
        conditional_default_effort: bool = False,
    ) -> tuple[str, str, int, str]:
        provider = state.providers.get(profile_id)
        if provider is None:
            raise ApiError(400, "unknown_provider", f"unknown provider: {profile_id}")
        configured = state.provider_profile(profile_id)
        selected_model_id = (
            requested_model or default_model or (configured.model if configured else "")
        )
        selected_effort = requested_effort
        try:
            models = await provider.list_models()
        except ProviderError as exc:
            raise ApiError(
                503,
                exc.code,
                str(exc),
                retryable=exc.retryable,
                details=dict(exc.details),
            ) from exc
        if not selected_model_id:
            if len(models) != 1:
                raise ApiError(
                    409,
                    "model_selection_required",
                    "provider did not report exactly one model",
                    details={"models": [item.id for item in models]},
                )
            selected_model_id = models[0].id
        selected = next((item for item in models if item.id == selected_model_id), None)
        if selected is None:
            raise ApiError(400, "unknown_model", f"unknown model: {selected_model_id}")
        efforts = selected.reasoning_efforts
        if (
            not efforts
            and configured
            and selected.id == configured.model
            and configured.supported_reasoning_efforts
        ):
            efforts = configured.supported_reasoning_efforts
        if not selected_effort:
            selected_effort = default_effort or (configured.reasoning_effort if configured else "")
            if conditional_default_effort:
                if selected_effort == "off" or selected.reasoning_supported is False:
                    selected_effort = "off"
                elif selected.reasoning_supported is None and not efforts:
                    selected_effort = "off"
                elif efforts == ["on"]:
                    selected_effort = "on"
        if (
            configured
            and configured.adapter in {"zai", "zai_coding"}
            and selected.id.lower().startswith("glm-5.3")
            and selected_effort == "off"
        ):
            if requested_effort == "off":
                raise ApiError(
                    400,
                    "reasoning_required",
                    "This GLM model requires reasoning; choose low, high, or max",
                )
            selected_effort = "low"
        if selected_effort and selected_effort != "off":
            if selected.reasoning_supported is False:
                raise ApiError(400, "reasoning_not_supported", "model does not advertise reasoning")
            if selected.reasoning_supported is None and not efforts:
                raise ApiError(
                    409,
                    "reasoning_capability_unknown",
                    "model reasoning capability is unknown until it is loaded or declared",
                )
            if efforts and selected_effort not in efforts:
                raise ApiError(
                    400,
                    "reasoning_effort_not_supported",
                    f"unsupported reasoning effort: {selected_effort}",
                    details={"supported": efforts},
                )
        if configured and configured.context_window_tokens is not None:
            context_window_tokens = configured.context_window_tokens
            context_window_source = "profile"
        elif selected.context_length is not None:
            context_window_tokens = selected.context_length
            context_window_source = "provider"
        else:
            context_window_tokens = state.config.context.fallback_window_tokens
            context_window_source = "fallback"
        return selected_model_id, selected_effort, context_window_tokens, context_window_source

    @app.get("/v1/workspaces", dependencies=auth, response_model=list[Workspace])
    async def list_workspaces() -> list[Workspace]:
        return await asyncio.to_thread(state.workspaces.list)

    async def register_explicit_workspace(workspace: Workspace) -> Workspace:
        """Persist execution trust only for a directory the user explicitly added."""
        try:
            await asyncio.to_thread(state.controls.grant_trust, Path(workspace.path))
        except (FileNotFoundError, OSError, ValueError, sqlite3.Error) as exc:
            raise ApiError(
                500,
                "workspace_trust_persistence_failed",
                f"workspace was registered but its trust grant could not be persisted: {exc}",
                retryable=True,
            ) from exc
        return workspace

    @app.post("/v1/workspaces", dependencies=auth, response_model=Workspace, status_code=201)
    async def create_workspace(request: WorkspaceCreateRequest) -> Workspace:
        try:
            workspace = await asyncio.to_thread(
                state.workspaces.register,
                Path(request.path),
                title=request.title,
                touch=False,
            )
            return await register_explicit_workspace(workspace)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ApiError(400, "invalid_workspace", str(exc)) from exc

    @app.patch("/v1/workspaces/{workspace_id}", dependencies=auth, response_model=Workspace)
    async def rename_workspace(workspace_id: str, request: WorkspaceRenameRequest) -> Workspace:
        try:
            return await asyncio.to_thread(state.workspaces.rename, workspace_id, request.title)
        except KeyError as exc:
            raise ApiError(404, "workspace_not_found", "workspace is not registered") from exc
        except ValueError as exc:
            raise ApiError(400, "invalid_workspace_title", str(exc)) from exc

    @app.delete("/v1/workspaces/{workspace_id}", dependencies=auth)
    async def delete_workspace(workspace_id: str) -> dict[str, bool]:
        deleted = await asyncio.to_thread(state.workspaces.delete, workspace_id)
        if not deleted:
            raise ApiError(404, "workspace_not_found", "workspace is not registered")
        return {"deleted": True}

    @app.get("/v1/directories", dependencies=auth, response_model=DirectoryListing)
    async def list_directories(path: str | None = None) -> DirectoryListing:
        try:
            return await asyncio.to_thread(
                state.workspaces.list_directory, Path(path) if path else None
            )
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ApiError(400, "invalid_directory", str(exc)) from exc

    @app.post("/v1/directories/pick", dependencies=auth, response_model=Workspace | None)
    async def pick_directory(request: DirectoryPickerRequest) -> Workspace | None:
        try:
            workspace = await asyncio.to_thread(
                state.workspaces.pick_directory,
                Path(request.initial_path) if request.initial_path else None,
            )
            return await register_explicit_workspace(workspace) if workspace is not None else None
        except NativeDirectoryPickerUnavailable as exc:
            raise ApiError(501, "native_directory_picker_unavailable", str(exc)) from exc
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise ApiError(500, "native_directory_picker_failed", str(exc)) from exc

    @app.post("/v1/directories/select", dependencies=auth)
    async def select_directory(request: DirectoryPickerRequest) -> dict[str, str] | None:
        try:
            selected = await asyncio.to_thread(
                state.workspaces.select_directory,
                Path(request.initial_path) if request.initial_path else None,
            )
            return {"path": str(selected)} if selected is not None else None
        except NativeDirectoryPickerUnavailable as exc:
            raise ApiError(501, "native_directory_picker_unavailable", str(exc)) from exc
        except (FileNotFoundError, OSError, RuntimeError, ValueError) as exc:
            raise ApiError(500, "native_directory_picker_failed", str(exc)) from exc

    @app.post("/v1/directories", dependencies=auth, response_model=Workspace, status_code=201)
    async def create_directory(request: DirectoryCreateRequest) -> Workspace:
        try:
            workspace = await asyncio.to_thread(
                state.workspaces.create_directory, Path(request.parent), request.name
            )
            return await register_explicit_workspace(workspace)
        except FileExistsError as exc:
            raise ApiError(409, "directory_exists", str(exc)) from exc
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ApiError(400, "invalid_directory", str(exc)) from exc

    @app.post("/v1/sessions", dependencies=auth, response_model=Session, status_code=201)
    async def create_session(request: CreateSessionRequest) -> Session:
        inherited: Session | None = None
        if request.inherit_session_id is not None:
            try:
                inherited = await asyncio.to_thread(
                    state.ledger.get_session, request.inherit_session_id
                )
            except KeyError as exc:
                raise ApiError(
                    404,
                    "session_not_found",
                    f"unknown session: {request.inherit_session_id}",
                ) from exc
        contextual: Session | None = None
        if inherited is None and not request.agent_id:
            try:
                contextual = await asyncio.to_thread(
                    state.ledger.latest_root_session, Path(request.working_directory)
                )
            except (FileNotFoundError, ValueError) as exc:
                raise ApiError(400, "invalid_working_directory", str(exc)) from exc
        agent_id = (
            inherited.agent_id
            if inherited is not None
            else request.agent_id
            or (contextual.agent_id if contextual is not None else "")
            or state.config.runtime.default_agent
        )
        try:
            await asyncio.to_thread(state.agents.load, agent_id)
        except (FileNotFoundError, ValueError) as exc:
            if contextual is not None and not request.agent_id:
                agent_id = state.config.runtime.default_agent
                try:
                    await asyncio.to_thread(state.agents.load, agent_id)
                except (FileNotFoundError, ValueError) as fallback_exc:
                    raise ApiError(400, "unknown_agent", str(fallback_exc)) from fallback_exc
            else:
                raise ApiError(400, "unknown_agent", str(exc)) from exc
        capsule = await asyncio.to_thread(state.agents.load, agent_id)
        agent_id = capsule.metadata.id
        if (
            inherited is None
            and capsule.metadata.execution is not None
            and not (request.provider or request.model)
        ):
            try:
                (
                    provider_name,
                    model,
                    reasoning_effort,
                    context_window_tokens,
                    context_window_source,
                ) = await resolve_agent_execution(
                    capsule.metadata.execution, state.providers, state.config
                )
            except (ValueError, ProviderError) as exc:
                raise ApiError(400, "invalid_agent_default_model", str(exc)) from exc
            interaction_mode = state.config.runtime.default_interaction_mode
        elif inherited is None:
            provider_name = request.provider or state.config.runtime.default_provider
            selection = await resolve_selection(
                provider_name,
                request.model,
                request.reasoning_effort,
                default_model=state.config.runtime.default_model,
                default_effort=state.config.runtime.default_reasoning_effort,
                conditional_default_effort=True,
            )
            model, reasoning_effort, context_window_tokens, context_window_source = selection
            interaction_mode = state.config.runtime.default_interaction_mode
        else:
            provider_name = inherited.provider
            model = inherited.model
            reasoning_effort = inherited.reasoning_effort
            context_window_tokens = inherited.context_window_tokens
            context_window_source = inherited.context_window_source
            interaction_mode = inherited.interaction_mode
        try:
            session = await asyncio.to_thread(
                state.ledger.create_session,
                working_directory=Path(request.working_directory),
                agent_id=agent_id,
                provider=provider_name,
                model=model,
                reasoning_effort=reasoning_effort,
                context_window_tokens=context_window_tokens,
                context_window_source=context_window_source,
                title=request.title,
                interaction_mode=interaction_mode,
            )
            return session
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(400, "invalid_working_directory", str(exc)) from exc

    @app.get("/v1/sessions", dependencies=auth, response_model=list[Session])
    async def list_sessions(
        include_delegated: bool = True,
        has_messages: bool | None = None,
        include_titled: bool = False,
        working_directory: str | None = None,
        registered_workspaces_only: bool = False,
    ) -> list[Session]:
        try:
            sessions = await asyncio.to_thread(
                state.ledger.list_sessions,
                has_messages=has_messages,
                include_titled=include_titled,
                working_directory=Path(working_directory) if working_directory else None,
            )
            if not include_delegated:
                sessions = [session for session in sessions if session.lineage_kind != "delegation"]
            if not registered_workspaces_only:
                return sessions
            authorized_paths = {
                workspace.path for workspace in await asyncio.to_thread(state.workspaces.list)
            }
            return [
                session for session in sessions if session.working_directory in authorized_paths
            ]
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(400, "invalid_working_directory", str(exc)) from exc

    @app.get("/v1/sessions/recent", dependencies=auth, response_model=Session | None)
    async def recent_session(
        working_directory: Annotated[str, Query(min_length=1)],
        active_within_seconds: Annotated[int, Query(ge=60, le=31_536_000)] = 604_800,
    ) -> Session | None:
        try:
            return await asyncio.to_thread(
                state.ledger.recent_open_session,
                Path(working_directory),
                active_within_seconds=active_within_seconds,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(400, "invalid_working_directory", str(exc)) from exc

    @app.get("/v1/agents", dependencies=auth, response_model=list[AgentPublic])
    async def list_agents() -> list[AgentPublic]:
        return [_agent_public(item) for item in await asyncio.to_thread(state.agents.list)]

    @app.get("/v1/tools", dependencies=auth, response_model=list[str])
    async def list_tools() -> list[str]:
        """Return every tool currently available to an agent capsule."""
        return sorted(state.runs.tools.names() | state.plugins.names() | state.mcp.names())

    @app.get(
        "/v1/agents/{agent_id}/capabilities",
        dependencies=auth,
        response_model=AgentCapabilities,
    )
    async def get_agent_capabilities(
        agent_id: str,
        working_directory: Annotated[str, Query(min_length=1)],
    ) -> AgentCapabilities:
        try:
            capsule = await asyncio.to_thread(state.agents.load, agent_id)
            agent_id = capsule.metadata.id
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(404, "agent_not_found", str(exc)) from exc
        workspace = await asyncio.to_thread(
            lambda: str(Path(working_directory).expanduser().resolve(strict=False))
        )
        editor_session = Session(
            id="agent-editor",
            created_at="1970-01-01T00:00:00+00:00",
            closed_at=None,
            status="open",
            title=None,
            working_directory=workspace,
            agent_id=agent_id,
            provider="",
            model="",
            reasoning_effort="",
            context_window_tokens=0,
            context_window_source="agent_editor",
            parent_session_id=None,
            fork_event_id=None,
            lineage_kind="root",
            delegation_depth=0,
            interaction_mode="auto",
        )
        skills = await asyncio.to_thread(
            state.runs.skills.visible, editor_session, query="", limit=200
        )
        return AgentCapabilities(
            tools=sorted(state.runs.tools.names() | state.plugins.names() | state.mcp.names()),
            skills=skills,
        )

    @app.get("/v1/agents/{agent_id}", dependencies=auth, response_model=AgentDetail)
    async def get_agent(agent_id: str) -> AgentDetail:
        try:
            return _agent_detail(await asyncio.to_thread(state.agents.load, agent_id))
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(404, "agent_not_found", str(exc)) from exc

    @app.post("/v1/agents", dependencies=auth, response_model=AgentDetail, status_code=201)
    async def create_agent(request: AgentCreateRequest) -> AgentDetail:
        try:
            capsule = await asyncio.to_thread(
                state.agents.create,
                request.name,
                authority=request.authority,
                source=request.source,
            )
            return _agent_detail(capsule)
        except FileExistsError as exc:
            raise ApiError(409, "agent_exists", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(400, "invalid_agent", str(exc)) from exc

    @app.patch("/v1/agents/{agent_id}", dependencies=auth, response_model=AgentDetail)
    async def update_agent(agent_id: str, request: AgentUpdateRequest) -> AgentDetail:
        try:
            if request.default_model is not None:
                current = await asyncio.to_thread(state.agents.load, agent_id)
                if current.metadata.execution != request.default_model:
                    await resolve_agent_execution(
                        request.default_model, state.providers, state.config
                    )
            capsule = await asyncio.to_thread(
                state.agents.update,
                agent_id,
                name=request.name,
                instructions=request.instructions,
                tools=request.tools,
                skills=request.skills,
                avatar=request.avatar,
                source=request.source,
                default_model=request.default_model,
                update_default_model="default_model" in request.model_fields_set,
            )
            return _agent_detail(capsule)
        except FileNotFoundError as exc:
            raise ApiError(404, "agent_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(400, "invalid_agent", str(exc)) from exc
        except ProviderError as exc:
            raise ApiError(400, "invalid_agent_default_model", str(exc)) from exc

    @app.post("/v1/agents/{agent_id}/validate", dependencies=auth, response_model=AgentDetail)
    async def validate_agent(agent_id: str) -> AgentDetail:
        return await get_agent(agent_id)

    @app.get("/v1/agents/{agent_id}/usage", dependencies=auth, response_model=AgentUsageProjection)
    async def get_agent_usage(agent_id: str) -> AgentUsageProjection:
        try:
            capsule = await asyncio.to_thread(state.agents.load, agent_id)
            agent_id = capsule.metadata.id
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(404, "agent_not_found", str(exc)) from exc
        return await asyncio.to_thread(agent_usage, state.ledger, agent_id)

    @app.delete("/v1/agents/{agent_id}", dependencies=auth)
    async def retire_agent(agent_id: str) -> dict[str, str]:
        try:
            retired = await asyncio.to_thread(state.agents.retire, agent_id)
            return {"retired_to": str(retired)}
        except FileNotFoundError as exc:
            raise ApiError(404, "agent_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(409, "agent_retirement_rejected", str(exc)) from exc

    @app.get("/v1/plugins", dependencies=auth, response_model=list[PluginView])
    async def list_plugins() -> list[PluginView]:
        return await asyncio.to_thread(state.plugins.list_plugins)

    @app.get("/v1/mcp/servers", dependencies=auth, response_model=list[McpServerView])
    async def list_mcp_servers() -> list[McpServerView]:
        return await asyncio.to_thread(state.mcp.list)

    @app.post(
        "/v1/mcp/servers",
        dependencies=auth,
        response_model=McpServerView,
        status_code=201,
    )
    async def add_mcp_server(request: McpServerSpec) -> McpServerView:
        try:
            return await state.mcp.add(request)
        except sqlite3.IntegrityError as exc:
            raise ApiError(
                409, "mcp_server_exists", f"MCP server already exists: {request.id}"
            ) from exc
        except (OSError, ValueError) as exc:
            raise ApiError(400, "invalid_mcp_server", str(exc)) from exc

    @app.get("/v1/mcp/servers/{server_id}", dependencies=auth, response_model=McpServerView)
    async def get_mcp_server(server_id: str) -> McpServerView:
        try:
            return await asyncio.to_thread(state.mcp.describe, server_id)
        except KeyError as exc:
            raise ApiError(404, "mcp_server_not_found", f"unknown MCP server: {server_id}") from exc

    @app.post(
        "/v1/mcp/servers/{server_id}/inspect",
        dependencies=auth,
        response_model=McpServerView,
    )
    async def inspect_mcp_server(server_id: str) -> McpServerView:
        try:
            return await state.mcp.inspect(server_id)
        except KeyError as exc:
            raise ApiError(404, "mcp_server_not_found", f"unknown MCP server: {server_id}") from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise ApiError(409, "mcp_inspection_failed", str(exc)) from exc

    @app.post(
        "/v1/mcp/servers/{server_id}/enable",
        dependencies=auth,
        response_model=McpServerView,
    )
    async def enable_mcp_server(server_id: str) -> McpServerView:
        try:
            return await state.mcp.enable(server_id)
        except KeyError as exc:
            raise ApiError(404, "mcp_server_not_found", f"unknown MCP server: {server_id}") from exc
        except (OSError, RuntimeError, ValueError) as exc:
            raise ApiError(409, "mcp_enable_failed", str(exc)) from exc

    @app.post(
        "/v1/mcp/servers/{server_id}/disable",
        dependencies=auth,
        response_model=McpServerView,
    )
    async def disable_mcp_server(server_id: str) -> McpServerView:
        try:
            return await state.mcp.disable(server_id)
        except KeyError as exc:
            raise ApiError(404, "mcp_server_not_found", f"unknown MCP server: {server_id}") from exc
        except RuntimeError as exc:
            raise ApiError(409, "mcp_server_busy", str(exc)) from exc

    @app.delete("/v1/mcp/servers/{server_id}", dependencies=auth)
    async def remove_mcp_server(server_id: str) -> dict[str, bool]:
        try:
            await state.mcp.remove(server_id)
        except KeyError as exc:
            raise ApiError(404, "mcp_server_not_found", f"unknown MCP server: {server_id}") from exc
        except RuntimeError as exc:
            raise ApiError(409, "mcp_server_busy", str(exc)) from exc
        return {"removed": True}

    @app.get("/v1/plugins/proposals", dependencies=auth, response_model=list[PluginProposalView])
    async def list_plugin_proposals() -> list[PluginProposalView]:
        return await asyncio.to_thread(state.plugins.list_proposals)

    @app.get(
        "/v1/plugins/proposals/{proposal_id}",
        dependencies=auth,
        response_model=PluginProposalView,
    )
    async def get_plugin_proposal(proposal_id: str) -> PluginProposalView:
        try:
            return await asyncio.to_thread(state.plugins.describe_proposal, proposal_id)
        except KeyError as exc:
            raise ApiError(404, "plugin_proposal_not_found", str(exc)) from exc

    @app.get("/v1/plugins/{plugin_id}", dependencies=auth, response_model=PluginView)
    async def get_plugin(plugin_id: str) -> PluginView:
        try:
            return await asyncio.to_thread(state.plugins.describe, plugin_id)
        except KeyError as exc:
            raise ApiError(404, "plugin_not_found", f"unknown plugin: {plugin_id}") from exc

    @app.post("/v1/plugins/inspect", dependencies=auth, response_model=PluginInspectView)
    async def inspect_plugin(request: PluginPathRequest) -> PluginInspectView:
        try:
            return await asyncio.to_thread(state.plugins.inspect, Path(request.path))
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise ApiError(400, "invalid_plugin", str(exc)) from exc

    @app.post(
        "/v1/plugins/uploads",
        dependencies=auth,
        response_model=PluginUploadInspection,
        status_code=201,
    )
    async def inspect_plugin_upload(request: PluginUploadRequest) -> PluginUploadInspection:
        upload_id = uuid4().hex
        upload_root = state.paths.plugin_uploads / upload_id
        try:
            await asyncio.to_thread(_write_plugin_upload, upload_root, request.files)
            inspected = await asyncio.to_thread(state.plugins.inspect, upload_root)
            return PluginUploadInspection(upload_id=upload_id, plugin=inspected)
        except (binascii.Error, FileNotFoundError, ValueError, OSError) as exc:
            await asyncio.to_thread(shutil.rmtree, upload_root, True)
            raise ApiError(400, "invalid_plugin", str(exc)) from exc

    @app.post(
        "/v1/plugins/uploads/{upload_id}/install",
        dependencies=auth,
        response_model=PluginView,
        status_code=201,
    )
    async def install_plugin_upload(upload_id: str) -> PluginView:
        upload_root = _plugin_upload_root(state.paths, upload_id)
        try:
            return await state.plugins.install(upload_root)
        except FileExistsError as exc:
            raise ApiError(409, "plugin_exists", str(exc)) from exc
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise ApiError(400, "invalid_plugin", str(exc)) from exc
        finally:
            await asyncio.to_thread(shutil.rmtree, upload_root, True)

    @app.delete("/v1/plugins/uploads/{upload_id}", dependencies=auth)
    async def discard_plugin_upload(upload_id: str) -> dict[str, bool]:
        upload_root = _plugin_upload_root(state.paths, upload_id)
        await asyncio.to_thread(shutil.rmtree, upload_root, True)
        return {"discarded": True}

    @app.post(
        "/v1/plugins/install",
        dependencies=auth,
        response_model=PluginView,
        status_code=201,
    )
    async def install_plugin(request: PluginPathRequest) -> PluginView:
        try:
            return await state.plugins.install(Path(request.path))
        except FileExistsError as exc:
            raise ApiError(409, "plugin_exists", str(exc)) from exc
        except (FileNotFoundError, ValueError, OSError) as exc:
            raise ApiError(400, "invalid_plugin", str(exc)) from exc

    @app.post("/v1/plugins/{plugin_id}/enable", dependencies=auth, response_model=PluginView)
    async def enable_plugin(plugin_id: str) -> PluginView:
        try:
            return await state.plugins.enable(plugin_id)
        except KeyError as exc:
            raise ApiError(404, "plugin_not_found", f"unknown plugin: {plugin_id}") from exc
        except (PluginSandboxError, PluginProtocolError, ValueError) as exc:
            raise ApiError(409, "plugin_enable_failed", str(exc)) from exc

    @app.post("/v1/plugins/{plugin_id}/disable", dependencies=auth, response_model=PluginView)
    async def disable_plugin(plugin_id: str) -> PluginView:
        try:
            return await state.plugins.disable(plugin_id)
        except KeyError as exc:
            raise ApiError(404, "plugin_not_found", f"unknown plugin: {plugin_id}") from exc

    @app.delete("/v1/plugins/{plugin_id}", dependencies=auth)
    async def remove_plugin(plugin_id: str) -> dict[str, bool]:
        try:
            await state.plugins.remove(plugin_id)
        except KeyError as exc:
            raise ApiError(404, "plugin_not_found", f"unknown plugin: {plugin_id}") from exc
        return {"removed": True}

    @app.get("/v1/sessions/{session_id}", dependencies=auth, response_model=Session)
    async def get_session(session_id: str) -> Session:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        return await state.runs.ensure_provider_context_window(session)

    @app.get(
        "/v1/sessions/{session_id}/environment",
        dependencies=auth,
        response_model=RuntimeEnvironmentSnapshot,
    )
    async def get_session_environment(session_id: str) -> RuntimeEnvironmentSnapshot:
        try:
            return await state.runs.environment_snapshot(session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.get(
        "/v1/sessions/{session_id}/terminals",
        dependencies=auth,
        response_model=list[BackgroundTerminal],
    )
    async def background_terminals(session_id: str) -> list[BackgroundTerminal]:
        try:
            await asyncio.to_thread(state.ledger.get_session, session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        return [
            BackgroundTerminal.model_validate(item)
            for item in state.runs.background_terminals(session_id)
        ]

    @app.delete(
        "/v1/sessions/{session_id}/terminals",
        dependencies=auth,
        response_model=BackgroundTerminalsStopped,
    )
    async def stop_background_terminals(session_id: str) -> BackgroundTerminalsStopped:
        try:
            await asyncio.to_thread(state.ledger.get_session, session_id)
            count = await state.runs.stop_background_terminals(session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        return BackgroundTerminalsStopped(closed=count)

    @app.delete("/v1/sessions/{session_id}", dependencies=auth, response_model=Session)
    async def close_session(session_id: str) -> Session:
        if not await state.runs.finish_terminal_session(session_id):
            raise ApiError(
                409,
                "session_run_active",
                "cannot clear a session during an active run",
            )
        try:
            await state.runs.clear_queue(session_id)
            await state.runs.stop_background_terminals(
                session_id, reason="session_closed", announce=False
            )
            await state.runs.settle_background_terminals(session_id)
            return await asyncio.to_thread(state.ledger.close_session, session_id)
        except KeyError as exc:
            try:
                await asyncio.to_thread(state.ledger.get_session, session_id)
            except KeyError:
                raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
            raise ApiError(
                409,
                "session_not_open",
                "session is already closed",
            ) from exc

    async def session_trust(session_id: str) -> tuple[Session, object | None]:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        grant = await asyncio.to_thread(state.controls.get_trust, Path(session.working_directory))
        return session, grant

    @app.get("/v1/sessions/{session_id}/trust", dependencies=auth, response_model=TrustStatus)
    async def get_session_trust(session_id: str) -> TrustStatus:
        session, grant_value = await session_trust(session_id)
        from hames.control import TrustGrant

        grant = grant_value if isinstance(grant_value, TrustGrant) else None
        return TrustStatus(
            path=session.working_directory,
            trusted=grant is not None,
            grant_id=grant.id if grant else None,
            created_at=grant.created_at if grant else None,
        )

    @app.put("/v1/sessions/{session_id}/trust", dependencies=auth, response_model=TrustStatus)
    async def trust_session_root(session_id: str) -> TrustStatus:
        session, existing = await session_trust(session_id)
        grant = await asyncio.to_thread(state.controls.grant_trust, Path(session.working_directory))
        if existing is None:
            await asyncio.to_thread(
                state.ledger.append,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="trust.granted",
                payload={"path": session.working_directory},
                correlation_id=session.id,
            )
        return TrustStatus(
            path=session.working_directory,
            trusted=True,
            grant_id=grant.id,
            created_at=grant.created_at,
        )

    @app.delete("/v1/sessions/{session_id}/trust", dependencies=auth, response_model=TrustStatus)
    async def revoke_session_root(session_id: str) -> TrustStatus:
        session, existing = await session_trust(session_id)
        if state.runs.is_working_directory_active(session.working_directory):
            raise ApiError(409, "project_run_active", "cannot revoke trust during an active run")
        if existing is not None:
            await asyncio.to_thread(state.controls.revoke_trust, Path(session.working_directory))
            await asyncio.to_thread(
                state.ledger.append,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="trust.revoked",
                payload={"path": session.working_directory},
                correlation_id=session.id,
            )
        return TrustStatus(path=session.working_directory, trusted=False)

    @app.patch("/v1/sessions/{session_id}", dependencies=auth, response_model=Session)
    async def update_session(session_id: str, request: UpdateSessionRequest) -> Session:
        selection = await resolve_selection(
            request.provider, request.model, request.reasoning_effort
        )
        model, reasoning_effort, context_window_tokens, context_window_source = selection
        try:
            return await asyncio.to_thread(
                state.ledger.update_session_settings,
                session_id,
                provider=request.provider,
                model=model,
                reasoning_effort=reasoning_effort,
                context_window_tokens=context_window_tokens,
                context_window_source=context_window_source,
            )
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.put("/v1/sessions/{session_id}/agent", dependencies=auth, response_model=Session)
    async def update_session_agent(session_id: str, request: UpdateSessionAgentRequest) -> Session:
        if not await state.runs.finish_terminal_session(session_id):
            raise ApiError(409, "session_run_active", "cannot change agent during an active run")
        try:
            capsule = await asyncio.to_thread(state.agents.load, request.agent_id)
            selection = None
            if capsule.metadata.execution is not None:
                selection = await resolve_agent_execution(
                    capsule.metadata.execution, state.providers, state.config
                )
            session = await asyncio.to_thread(
                state.ledger.update_session_agent, session_id, agent_id=request.agent_id
            )
            if selection is not None:
                provider, model, effort, window, source = selection
                session = await asyncio.to_thread(
                    state.ledger.update_session_settings,
                    session_id,
                    provider=provider,
                    model=model,
                    reasoning_effort=effort,
                    context_window_tokens=window,
                    context_window_source=source,
                )
            return session
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(400, "unknown_agent", str(exc)) from exc

    @app.put("/v1/sessions/{session_id}/mode", dependencies=auth, response_model=Session)
    async def update_session_mode(session_id: str, request: UpdateSessionModeRequest) -> Session:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            if session.interaction_mode == "plan" and request.mode == "auto":
                plan = await state.runs.current_plan(session_id)
                if plan.current is not None and plan.current.status in {"ready", "failed"}:
                    await state.runs.execute_plan(session_id, strategy="keep")
                    return await asyncio.to_thread(state.ledger.get_session, session_id)
            return await asyncio.to_thread(
                state.ledger.update_session_mode, session_id, mode=request.mode
            )
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "mode_change_rejected", str(exc)) from exc

    @app.put("/v1/sessions/{session_id}/title", dependencies=auth, response_model=Session)
    async def update_session_title(session_id: str, request: UpdateSessionTitleRequest) -> Session:
        try:
            await asyncio.to_thread(
                state.ledger.update_session_title, session_id, title=request.title
            )
            return await asyncio.to_thread(state.ledger.get_session, session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        except ValueError as exc:
            raise ApiError(400, "invalid_session_title", str(exc)) from exc

    @app.put("/v1/sessions/{session_id}/pinned", dependencies=auth, response_model=Session)
    async def update_session_pinned(
        session_id: str, request: UpdateSessionPinnedRequest
    ) -> Session:
        try:
            return await asyncio.to_thread(
                state.ledger.update_session_pinned,
                session_id,
                pinned=request.pinned,
            )
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.get(
        "/v1/sessions/{session_id}/memories",
        dependencies=auth,
        response_model=list[MemoryRecord],
    )
    async def list_memories(
        session_id: str,
        query: str = "",
        status: str = "active",
        layer: MemoryLayer | None = None,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> list[MemoryRecord]:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            selected_status: MemoryStatus | None
            if status == "all":
                selected_status = None
            elif status in {"proposed", "active", "rejected", "superseded", "retracted"}:
                selected_status = cast(MemoryStatus, status)
            else:
                raise ApiError(400, "invalid_memory_status", f"unknown memory status: {status}")
            return await asyncio.to_thread(
                state.memory.store.list_visible,
                session,
                status=selected_status,
                layer=layer,
                query=query,
                limit=limit,
                offset=offset,
            )
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.get(
        "/v1/sessions/{session_id}/memories/{memory_id}",
        dependencies=auth,
        response_model=MemoryRecord,
    )
    async def get_memory(session_id: str, memory_id: str) -> MemoryRecord:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            return await asyncio.to_thread(state.memory.store.get_visible, session, memory_id)
        except KeyError as exc:
            raise ApiError(404, "memory_not_found", f"unknown visible memory: {memory_id}") from exc

    @app.post(
        "/v1/sessions/{session_id}/memories",
        dependencies=auth,
        response_model=MemoryRecord,
        status_code=201,
    )
    async def create_memory(session_id: str, request: MemoryCreateRequest) -> MemoryRecord:
        encoded = "\n".join((request.subject, request.predicate, request.value, request.summary))
        if contains_secret(encoded):
            raise ApiError(
                400,
                "memory_secret_rejected",
                "explicit memory resembles a credential or private key",
            )
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            source = await asyncio.to_thread(
                state.ledger.append,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="memory.capture.requested",
                payload={"content": request.summary.strip(), "explicit": True},
                correlation_id=session.id,
            )
            mutation = await asyncio.to_thread(
                state.memory.store.create_candidate,
                session=session,
                candidate=MemoryCandidate(
                    layer=request.layer,
                    visibility=request.visibility,
                    subject=request.subject.strip(),
                    predicate=request.predicate.strip(),
                    value=request.value.strip(),
                    summary=request.summary.strip(),
                    confidence=1.0,
                    importance=0.8,
                    provenance_event_ids=[source.id],
                    evidence_basis="explicit_user",
                ),
                run_id=None,
                origin_kind="explicit",
                activate=True,
                causation_id=source.id,
            )
            for event in (source, *mutation.events):
                await state.broker.publish(
                    event.session_id,
                    {"durable": True, "event": event.model_dump(mode="json")},
                )
            return mutation.record
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        except ValueError as exc:
            raise ApiError(400, "invalid_memory", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/memories/capture",
        dependencies=auth,
        response_model=MemoryJob,
        status_code=202,
    )
    async def capture_memory(session_id: str, request: MemoryCaptureRequest) -> MemoryJob:
        if contains_secret(request.content):
            raise ApiError(
                400,
                "memory_secret_rejected",
                "explicit memory resembles a credential or private key",
            )
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            source = await asyncio.to_thread(
                state.ledger.append,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="memory.capture.requested",
                payload={"content": request.content, "explicit": True},
                correlation_id=session.id,
            )
            await state.broker.publish(
                source.session_id,
                {"durable": True, "event": source.model_dump(mode="json")},
            )
            job = await state.memory.enqueue_capture(session, request.content, source)
            await state.runs.ensure_work_title(session_id, "Remember")
            return job
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.post(
        "/v1/sessions/{session_id}/memories/{memory_id}/transition",
        dependencies=auth,
        response_model=MemoryRecord,
    )
    async def transition_memory(
        session_id: str, memory_id: str, request: MemoryTransitionRequest
    ) -> MemoryRecord:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            mutation = await asyncio.to_thread(
                state.memory.store.transition,
                session=session,
                memory_id=memory_id,
                action=request.action,
                reason=request.reason,
            )
            for event in mutation.events:
                await state.broker.publish(
                    event.session_id,
                    {"durable": True, "event": event.model_dump(mode="json")},
                )
            return mutation.record
        except KeyError as exc:
            raise ApiError(404, "memory_not_found", f"unknown visible memory: {memory_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "invalid_memory_transition", str(exc)) from exc

    @app.delete(
        "/v1/sessions/{session_id}/memories/{memory_id}",
        dependencies=auth,
        response_model=MemoryDeleteResponse,
    )
    async def delete_memory(session_id: str, memory_id: str) -> MemoryDeleteResponse:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            event = await asyncio.to_thread(
                state.memory.store.delete,
                session=session,
                memory_id=memory_id,
                reason="user_request",
            )
            await state.broker.publish(
                event.session_id,
                {"durable": True, "event": event.model_dump(mode="json")},
            )
            return MemoryDeleteResponse(memory_id=memory_id, deleted=True)
        except KeyError as exc:
            raise ApiError(404, "memory_not_found", f"unknown visible memory: {memory_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "invalid_memory_deletion", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/memories/{memory_id}/promote",
        dependencies=auth,
        response_model=MemoryRecord,
    )
    async def promote_memory(
        session_id: str, memory_id: str, request: MemoryPromotionRequest
    ) -> MemoryRecord:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            source = await asyncio.to_thread(
                state.ledger.append,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="memory.promotion.requested",
                payload={"memory_id": memory_id, "visibility": request.visibility},
                correlation_id=memory_id,
            )
            mutation = await asyncio.to_thread(
                state.memory.store.promote,
                session=session,
                memory_id=memory_id,
                visibility=request.visibility,
                causation_id=source.id,
            )
            for event in (source, *mutation.events):
                await state.broker.publish(
                    event.session_id,
                    {"durable": True, "event": event.model_dump(mode="json")},
                )
            return mutation.record
        except KeyError as exc:
            raise ApiError(404, "memory_not_found", f"unknown visible memory: {memory_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "invalid_memory_promotion", str(exc)) from exc

    @app.get(
        "/v1/sessions/{session_id}/memory-jobs",
        dependencies=auth,
        response_model=list[MemoryJob],
    )
    async def list_memory_jobs(session_id: str) -> list[MemoryJob]:
        try:
            await asyncio.to_thread(state.ledger.get_session, session_id)
            return await asyncio.to_thread(state.memory.store.list_jobs, session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.post(
        "/v1/sessions/{session_id}/memory-jobs/{job_id}/retry",
        dependencies=auth,
        response_model=MemoryJob,
        status_code=202,
    )
    async def retry_memory_job(session_id: str, job_id: str) -> MemoryJob:
        try:
            await asyncio.to_thread(state.ledger.get_session, session_id)
            job = await state.memory.retry(session_id, job_id)
            await state.runs.ensure_work_title(session_id, "Memory maintenance")
            return job
        except KeyError as exc:
            raise ApiError(404, "memory_job_not_found", f"unknown memory job: {job_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "memory_job_not_retryable", str(exc)) from exc

    @app.get(
        "/v1/sessions/{session_id}/skills",
        dependencies=auth,
        response_model=list[SkillSummary],
    )
    async def list_skills(
        session_id: str,
        query: str = "",
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[SkillSummary]:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            scoped = await asyncio.to_thread(
                state.runs.skills.visible, session, query="", limit=limit
            )
            ranked = await asyncio.to_thread(
                state.runs.skills.visible, session, query=query, limit=limit
            )
            by_slug = {item.slug: item for item in scoped}
            by_slug.update({item.slug: item for item in ranked})
            capsule = await asyncio.to_thread(state.agents.load, session.agent_id)
            return apply_agent_skill_policy(capsule, list(by_slug.values()), limit=limit)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.get(
        "/v1/sessions/{session_id}/skills/available",
        dependencies=auth,
        response_model=list[SkillSummary],
    )
    async def list_available_skills(session_id: str) -> list[SkillSummary]:
        """List workspace-visible Skills before applying the active agent's policy."""
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            return await asyncio.to_thread(state.runs.skills.catalog, session, limit=200)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.get(
        "/v1/sessions/{session_id}/skills/available/{slug}",
        dependencies=auth,
        response_model=SkillVersion,
    )
    async def get_available_skill(session_id: str, slug: str) -> SkillVersion:
        """Inspect a workspace-visible Skill without applying one agent's catalog policy."""
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            return await asyncio.to_thread(state.runs.skills.latest_visible, session, slug)
        except KeyError as exc:
            raise ApiError(404, "skill_not_found", f"unknown visible Skill: {slug}") from exc
        except ValueError as exc:
            raise ApiError(409, "skill_integrity_error", str(exc)) from exc

    @app.get(
        "/v1/sessions/{session_id}/skills/{slug}",
        dependencies=auth,
        response_model=SkillVersion,
    )
    async def get_skill(session_id: str, slug: str) -> SkillVersion:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            capsule = await asyncio.to_thread(state.agents.load, session.agent_id)
            if not skill_permitted(capsule, slug):
                raise KeyError(slug)
            return await asyncio.to_thread(state.runs.skills.get_visible, session, slug)
        except KeyError as exc:
            raise ApiError(404, "skill_not_found", f"unknown visible Skill: {slug}") from exc
        except ValueError as exc:
            raise ApiError(409, "skill_integrity_error", str(exc)) from exc

    @app.get(
        "/v1/sessions/{session_id}/skills/{slug}/history",
        dependencies=auth,
        response_model=list[SkillVersion],
    )
    async def skill_history(session_id: str, slug: str) -> list[SkillVersion]:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            current = await asyncio.to_thread(state.runs.skills.latest_visible, session, slug)
            return await asyncio.to_thread(state.runs.skills.history, current.skill_id)
        except KeyError as exc:
            raise ApiError(404, "skill_not_found", f"unknown visible Skill: {slug}") from exc

    @app.post(
        "/v1/sessions/{session_id}/skills/author",
        dependencies=auth,
        response_model=SkillJob,
        status_code=202,
    )
    async def author_skill(session_id: str, request: SkillAuthorRequest) -> SkillJob:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            job = await state.skills.author(
                session,
                goal=request.goal,
                scope=request.scope,
                target_skill_id=request.target_skill_id,
            )
            await state.runs.ensure_work_title(session_id, f"Skill: {request.goal}")
            return job
        except KeyError as exc:
            raise ApiError(404, "session_or_skill_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(409, "skill_authoring_not_queued", str(exc)) from exc

    @app.get(
        "/v1/sessions/{session_id}/skill-jobs",
        dependencies=auth,
        response_model=list[SkillJob],
    )
    async def list_skill_jobs(session_id: str) -> list[SkillJob]:
        try:
            await asyncio.to_thread(state.ledger.get_session, session_id)
            return await asyncio.to_thread(state.runs.skills.list_jobs, session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.post(
        "/v1/sessions/{session_id}/skill-jobs/{job_id}/retry",
        dependencies=auth,
        response_model=SkillJob,
        status_code=202,
    )
    async def retry_skill_job(session_id: str, job_id: str) -> SkillJob:
        try:
            await asyncio.to_thread(state.ledger.get_session, session_id)
            job = await state.skills.retry(session_id, job_id)
            await state.runs.ensure_work_title(session_id, "Skill maintenance")
            return job
        except KeyError as exc:
            raise ApiError(404, "skill_job_not_found", f"unknown Skill job: {job_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "skill_job_not_retryable", str(exc)) from exc

    @app.delete(
        "/v1/sessions/{session_id}/skills/{slug}",
        dependencies=auth,
        response_model=SkillDeleteResponse,
    )
    async def delete_skill(session_id: str, slug: str) -> SkillDeleteResponse:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            current = await asyncio.to_thread(state.runs.skills.latest_visible, session, slug)
            source = await asyncio.to_thread(
                state.ledger.append,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="skill.control.requested",
                payload={
                    "skill_id": current.skill_id,
                    "version_id": current.id,
                    "action": "delete",
                    "reason": "user_request",
                },
                correlation_id=current.skill_id,
            )
            await state.broker.publish(
                source.session_id, {"durable": True, "event": source.model_dump(mode="json")}
            )
            result = await asyncio.to_thread(state.runs.skills.delete, session, slug)
            event = await asyncio.to_thread(
                state.ledger.append,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="skill.deleted",
                payload={
                    "skill_id": current.skill_id,
                    "version_id": result.id,
                    "action": "delete",
                    "reason": "user_request",
                },
                causation_id=source.id,
                correlation_id=current.skill_id,
            )
            await state.broker.publish(
                event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
            )
            return SkillDeleteResponse(skill_id=current.skill_id, slug=slug, deleted=True)
        except KeyError as exc:
            raise ApiError(404, "skill_not_found", f"unknown visible Skill: {slug}") from exc
        except ValueError as exc:
            raise ApiError(409, "skill_delete_rejected", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/skills/{slug}/{action}",
        dependencies=auth,
        response_model=SkillVersion,
    )
    async def control_skill(
        session_id: str,
        slug: str,
        action: Literal["pin", "unpin", "archive", "restore", "rollback"],
        request: SkillControlRequest,
    ) -> SkillVersion:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            current = await asyncio.to_thread(state.runs.skills.latest_visible, session, slug)
            source = await asyncio.to_thread(
                state.ledger.append,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type="skill.control.requested",
                payload={
                    "skill_id": current.skill_id,
                    "version_id": current.id,
                    "action": action,
                    "reason": request.reason,
                },
                correlation_id=current.skill_id,
            )
            await state.broker.publish(
                source.session_id, {"durable": True, "event": source.model_dump(mode="json")}
            )
            if action == "rollback":
                active = await asyncio.to_thread(state.runs.skills.get_visible, session, slug)
                result, events = await asyncio.to_thread(
                    state.runs.skills.quarantine_and_rollback,
                    session,
                    active.id,
                    reason=request.reason,
                    causation_id=source.id,
                )
                for event in events:
                    await state.broker.publish(
                        event.session_id,
                        {"durable": True, "event": event.model_dump(mode="json")},
                    )
                return result
            if action in {"pin", "unpin"}:
                result = await asyncio.to_thread(
                    state.runs.skills.set_pinned, session, slug, pinned=action == "pin"
                )
            else:
                result = await asyncio.to_thread(
                    state.runs.skills.set_archived,
                    session,
                    slug,
                    archived=action == "archive",
                )
            event = await asyncio.to_thread(
                state.ledger.append,
                session_id=session.id,
                agent_id=session.agent_id,
                event_type={
                    "pin": "skill.pinned",
                    "unpin": "skill.unpinned",
                    "archive": "skill.archived",
                    "restore": "skill.restored",
                }[action],
                payload={
                    "skill_id": current.skill_id,
                    "version_id": result.id,
                    "action": action,
                    "reason": request.reason,
                },
                causation_id=source.id,
                correlation_id=current.skill_id,
            )
            await state.broker.publish(
                event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
            )
            return result
        except KeyError as exc:
            raise ApiError(404, "skill_not_found", f"unknown visible Skill: {slug}") from exc
        except ValueError as exc:
            raise ApiError(409, "invalid_skill_transition", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/correct",
        dependencies=auth,
        response_model=Scar,
        status_code=201,
    )
    async def submit_correction(session_id: str, request: CorrectionRequest) -> Scar:
        try:
            await asyncio.to_thread(state.ledger.get_session, session_id)
            return await state.evolution.submit_correction(
                session_id,
                content=request.content,
                target_event_id=request.target_event_id,
            )
        except KeyError as exc:
            raise ApiError(404, "session_or_event_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(400, "invalid_correction", str(exc)) from exc

    @app.get("/v1/sessions/{session_id}/scars", dependencies=auth, response_model=list[Scar])
    async def list_scars(
        session_id: str,
        status: ScarStatus | None = None,
        limit: int = Query(default=100, ge=1, le=200),
    ) -> list[Scar]:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            return await asyncio.to_thread(
                state.evolution.store.list_scars, session, status=status, limit=limit
            )
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        except ValueError as exc:
            raise ApiError(422, "invalid_status_filter", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/scars",
        dependencies=auth,
        response_model=Scar,
        status_code=201,
    )
    async def create_scar(session_id: str, request: ScarCreateRequest) -> Scar:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            recorded = await asyncio.to_thread(
                state.evolution.store.record_candidate,
                session=session,
                title=request.title,
                severity=request.severity,
                failure_signature=request.failure_signature,
                description=request.description,
                expected_behavior=request.expected_behavior,
                evidence_event_ids=[],
                detection="manual",
                scope=request.scope,
            )
            opened = await asyncio.to_thread(
                state.evolution.store.open,
                session=session,
                scar_id=recorded.scar.id,
                reason="created manually",
            )
            for event in (*recorded.events, *opened.events):
                await state.broker.publish(
                    event.session_id,
                    {"durable": True, "event": event.model_dump(mode="json")},
                )
            return opened.scar
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        except ValueError as exc:
            raise ApiError(400, "invalid_scar", str(exc)) from exc

    @app.get("/v1/sessions/{session_id}/scars/{scar_id}", dependencies=auth, response_model=Scar)
    async def get_scar(session_id: str, scar_id: str) -> Scar:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            return await asyncio.to_thread(state.evolution.store.get_visible, session, scar_id)
        except KeyError as exc:
            raise ApiError(404, "scar_not_found", f"unknown visible Scar: {scar_id}") from exc

    @app.patch(
        "/v1/sessions/{session_id}/scars/{scar_id}",
        dependencies=auth,
        response_model=Scar,
    )
    async def update_scar(session_id: str, scar_id: str, request: ScarUpdateRequest) -> Scar:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            mutation = await asyncio.to_thread(
                state.evolution.store.edit,
                session=session,
                scar_id=scar_id,
                title=request.title,
                severity=request.severity,
                description=request.description,
                expected_behavior=request.expected_behavior,
            )
            for event in mutation.events:
                await state.broker.publish(
                    event.session_id,
                    {"durable": True, "event": event.model_dump(mode="json")},
                )
            return mutation.scar
        except KeyError as exc:
            raise ApiError(404, "scar_not_found", f"unknown visible Scar: {scar_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "invalid_scar_edit", str(exc)) from exc

    @app.delete(
        "/v1/sessions/{session_id}/scars/{scar_id}",
        dependencies=auth,
        response_model=ScarDeleteResponse,
    )
    async def delete_scar(session_id: str, scar_id: str) -> ScarDeleteResponse:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            event = await asyncio.to_thread(
                state.evolution.store.delete,
                session=session,
                scar_id=scar_id,
                reason="user_request",
            )
            await state.broker.publish(
                event.session_id,
                {"durable": True, "event": event.model_dump(mode="json")},
            )
            return ScarDeleteResponse(scar_id=scar_id, deleted=True)
        except KeyError as exc:
            raise ApiError(404, "scar_not_found", f"unknown visible Scar: {scar_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "invalid_scar_deletion", str(exc)) from exc

    @app.get(
        "/v1/sessions/{session_id}/scars/{scar_id}/inspection",
        dependencies=auth,
        response_model=ScarInspection,
    )
    async def inspect_scar_lineage(session_id: str, scar_id: str) -> ScarInspection:
        try:
            return await asyncio.to_thread(
                inspect_scar, state.ledger, state.evolution.store, session_id, scar_id
            )
        except KeyError as exc:
            raise ApiError(404, "scar_not_found", f"unknown visible Scar: {scar_id}") from exc

    @app.post(
        "/v1/sessions/{session_id}/context-rules",
        dependencies=auth,
        response_model=ContextRule,
        status_code=201,
    )
    async def propose_context_rule(session_id: str, request: ContextRuleRequest) -> ContextRule:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            mutation = await asyncio.to_thread(
                state.runs.context_rules.propose,
                session=session,
                description=request.description,
                require_source_types=request.require_source_types,
                condition=ContextRuleCondition(
                    workspace_paths=request.workspace_paths,
                    agent_ids=request.agent_ids,
                ),
                scar_id=request.scar_id,
            )
            for event in mutation.events:
                await state.broker.publish(
                    event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
                )
            result = mutation.rule
            assert isinstance(result, ContextRule)
            return result
        except ValueError as exc:
            raise ApiError(400, "invalid_context_rule", str(exc)) from exc

    @app.get("/v1/context-rules", dependencies=auth, response_model=list[ContextRule])
    async def list_context_rules(status: str | None = None) -> list[ContextRule]:
        return await asyncio.to_thread(
            state.runs.context_rules.list_rules, status=_rule_status_filter(status)
        )

    @app.post("/v1/context-rules/{rule_id}/{action}", dependencies=auth)
    async def decide_context_rule(rule_id: str, action: str, request: RuleDecisionRequest):
        rule_action = _rule_action(action, "context-rule")
        try:
            mutation = await asyncio.to_thread(
                state.runs.context_rules.set_status,
                rule_id=rule_id,
                action=rule_action,
                reason=request.reason,
            )
        except KeyError as exc:
            raise ApiError(404, "context_rule_not_found", f"unknown rule: {rule_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "invalid_context_rule_transition", str(exc)) from exc
        for event in mutation.events:
            await state.broker.publish(
                event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
            )
        result = mutation.rule
        assert isinstance(result, ContextRule)
        return result

    @app.post(
        "/v1/sessions/{session_id}/policy-rules",
        dependencies=auth,
        response_model=PolicyRule,
        status_code=201,
    )
    async def propose_policy_rule(session_id: str, request: PolicyRuleRequest) -> PolicyRule:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            mutation = await asyncio.to_thread(
                state.runs.policy_rules.propose,
                session=session,
                action=request.action,
                pattern=request.pattern,
                reason=request.reason,
                scar_id=request.scar_id,
            )
            for event in mutation.events:
                await state.broker.publish(
                    event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
                )
            result = mutation.rule
            assert isinstance(result, PolicyRule)
            return result
        except ValueError as exc:
            raise ApiError(400, "invalid_policy_rule", str(exc)) from exc

    @app.get("/v1/policy-rules", dependencies=auth, response_model=list[PolicyRule])
    async def list_policy_rules(status: str | None = None) -> list[PolicyRule]:
        return await asyncio.to_thread(
            state.runs.policy_rules.list_rules, status=_rule_status_filter(status)
        )

    @app.post("/v1/policy-rules/{rule_id}/{action}", dependencies=auth)
    async def decide_policy_rule(rule_id: str, action: str, request: RuleDecisionRequest):
        rule_action = _rule_action(action, "policy-rule")
        try:
            mutation = await asyncio.to_thread(
                state.runs.policy_rules.set_status,
                rule_id=rule_id,
                action=rule_action,
                reason=request.reason,
            )
        except KeyError as exc:
            raise ApiError(404, "policy_rule_not_found", f"unknown rule: {rule_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "invalid_policy_rule_transition", str(exc)) from exc
        for event in mutation.events:
            await state.broker.publish(
                event.session_id, {"durable": True, "event": event.model_dump(mode="json")}
            )
        result = mutation.rule
        assert isinstance(result, PolicyRule)
        return result

    @app.get("/v1/sessions/{session_id}/events", dependencies=auth, response_model=list[Event])
    async def list_events(session_id: str, after_sequence: int = 0) -> list[Event]:
        return await asyncio.to_thread(
            state.ledger.list_events, session_id, after_sequence=after_sequence
        )

    @app.get("/v1/sessions/{session_id}/history", dependencies=auth, response_model=list[Event])
    async def replay_history(session_id: str, after_sequence: int = 0) -> list[Event]:
        try:
            return await asyncio.to_thread(
                state.ledger.replay, session_id, after_sequence=after_sequence
            )
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.get(
        "/v1/sessions/{session_id}/runs",
        dependencies=auth,
        response_model=list[RunSummary],
    )
    async def inspect_session_runs(session_id: str) -> list[RunSummary]:
        try:
            return await asyncio.to_thread(session_runs, state.ledger, session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.get(
        "/v1/sessions/{session_id}/usage",
        dependencies=auth,
        response_model=UsageProjection,
    )
    async def inspect_session_usage(session_id: str) -> UsageProjection:
        try:
            usage = await asyncio.to_thread(session_usage, state.ledger, session_id)
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            usage.daily_activity = await asyncio.to_thread(
                workspace_daily_usage,
                state.ledger,
                session.working_directory,
            )
            provider = state.providers.get(session.provider)
            await _attach_codex_account_usage(usage, provider)
            grok = next((p for p in state.providers.values() if p.adapter == "grok"), None)
            await _attach_grok_account_usage(usage, grok)
            return usage
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.get("/v1/usage", dependencies=auth, response_model=UsageProjection)
    async def inspect_pooled_usage() -> UsageProjection:
        usage = await asyncio.to_thread(pooled_usage, state.ledger, days=365)
        preferred = state.providers.get(state.config.runtime.default_provider)
        provider = (
            preferred
            if preferred is not None and preferred.adapter == "codex"
            else next(
                (
                    candidate
                    for candidate in state.providers.values()
                    if candidate.adapter == "codex"
                ),
                None,
            )
        )
        grok = next(
            (candidate for candidate in state.providers.values() if candidate.adapter == "grok"),
            None,
        )
        await asyncio.gather(
            _attach_codex_account_usage(usage, provider),
            _attach_grok_account_usage(usage, grok),
        )
        return usage

    @app.get("/v1/runs/{run_id}/inspection", dependencies=auth, response_model=RunInspection)
    async def inspect_run_endpoint(run_id: str) -> RunInspection:
        try:
            return await asyncio.to_thread(inspect_run, state.ledger, run_id)
        except KeyError as exc:
            raise ApiError(404, "run_not_found", f"unknown run: {run_id}") from exc

    @app.get("/v1/contexts/{event_id}", dependencies=auth, response_model=ContextInspection)
    async def inspect_context_endpoint(event_id: str) -> ContextInspection:
        try:
            return await asyncio.to_thread(inspect_context, state.ledger, event_id)
        except KeyError as exc:
            raise ApiError(404, "context_not_found", f"unknown context: {event_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "invalid_context_manifest", str(exc)) from exc

    @app.get("/v1/sessions/{session_id}/transcript", dependencies=auth)
    async def session_transcript(
        session_id: str, format: Literal["markdown", "jsonl"] = "markdown"
    ) -> Response:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            content = await asyncio.to_thread(export_transcript, state.ledger, session, format)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        media_type = "text/markdown" if format == "markdown" else "application/x-ndjson"
        return Response(content=content, media_type=media_type)

    @app.post(
        "/v1/sessions/{session_id}/fork",
        dependencies=auth,
        response_model=Session,
        status_code=201,
    )
    async def fork_session(session_id: str, request: ForkSessionRequest) -> Session:
        if state.runs.is_session_active(session_id):
            raise ApiError(409, "session_run_active", "cannot fork a session with an active run")
        fork_event_id: str | None = None
        if request.at is not None:
            try:
                fork_event_id = (
                    await asyncio.to_thread(
                        state.ledger.resolve_visible_event, session_id, request.at
                    )
                ).id
            except KeyError as exc:
                raise ApiError(404, "fork_event_not_found", str(exc)) from exc
        try:
            if request.agent_id is not None:
                await asyncio.to_thread(state.agents.load, request.agent_id)
            return await asyncio.to_thread(
                state.ledger.fork_session,
                session_id,
                fork_event_id=fork_event_id,
                title=request.title,
                agent_id=request.agent_id,
            )
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "invalid_fork", str(exc)) from exc

    @app.get("/v1/events/{event_id}", dependencies=auth, response_model=Event)
    async def get_event(event_id: str) -> Event:
        try:
            return await asyncio.to_thread(state.ledger.get_event, event_id)
        except KeyError as exc:
            raise ApiError(404, "event_not_found", f"unknown event: {event_id}") from exc

    @app.get("/v1/sessions/{session_id}/attachments/{digest}", dependencies=auth)
    async def get_message_attachment(session_id: str, digest: str) -> Response:
        try:
            events = await asyncio.to_thread(state.ledger.list_events, session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        reference: dict[str, object] | None = None
        for event in events:
            raw_value = event.payload.get("attachments", [])
            if not isinstance(raw_value, list):
                continue
            raw_attachments = cast(list[object], raw_value)
            for raw_attachment in raw_attachments:
                if not isinstance(raw_attachment, dict):
                    continue
                item = cast(dict[str, object], raw_attachment)
                if item.get("digest") == digest:
                    reference = item
        if reference is None:
            raise ApiError(404, "attachment_not_found", "unknown session attachment")
        try:
            content = await asyncio.to_thread(state.ledger.blob_store.read, digest)
        except (BlobIntegrityError, ValueError) as exc:
            raise ApiError(409, "attachment_unavailable", str(exc)) from exc
        media_type = str(reference.get("media_type", "application/octet-stream"))
        return Response(content=content, media_type=media_type)

    @app.get(
        "/v1/events/{event_id}/tool-result-details",
        dependencies=auth,
        response_model=ToolResultDetails,
    )
    async def get_tool_result_details(event_id: str) -> ToolResultDetails:
        try:
            event = await asyncio.to_thread(state.ledger.get_event, event_id)
        except KeyError as exc:
            raise ApiError(404, "event_not_found", f"unknown event: {event_id}") from exc
        if event.type not in {"tool.completed", "tool.failed", "tool.rejected"}:
            raise ApiError(
                409,
                "event_has_no_tool_result",
                f"event {event_id} is not a durable tool result",
            )
        content = str(event.payload.get("content", ""))
        references = cast(list[object], event.payload.get("blob_references", []))
        retained = False
        if bool(event.payload.get("truncated")) and references:
            digest = references[0]
            if not isinstance(digest, str):
                raise ApiError(409, "tool_result_details_unavailable", "invalid blob reference")
            try:
                encoded = await asyncio.to_thread(state.ledger.blob_store.read, digest)
                content = encoded.decode()
                retained = True
            except (BlobIntegrityError, UnicodeDecodeError, ValueError) as exc:
                raise ApiError(409, "tool_result_details_unavailable", str(exc)) from exc
        return _bounded_tool_result_details(event, content, retained=retained)

    @app.get("/v1/events/{event_id}/verify", dependencies=auth, response_model=IntegrityResult)
    async def verify_event(event_id: str) -> IntegrityResult:
        try:
            return await asyncio.to_thread(state.ledger.verify_event, event_id)
        except KeyError as exc:
            raise ApiError(404, "event_not_found", f"unknown event: {event_id}") from exc

    @app.post(
        "/v1/sessions/{session_id}/messages",
        dependencies=auth,
        response_model=MessageAccepted,
        status_code=202,
    )
    async def send_message(session_id: str, request: MessageRequest) -> MessageAccepted:
        try:
            attachments = await state.runs.admit_attachments(session_id, request.attachments)
            result = await state.runs.submit(
                session_id,
                request.content,
                remember=request.remember,
                paste_spans=[span.model_dump(mode="json") for span in request.paste_spans],
                send_now=request.send_now,
                purpose=request.purpose,
                submission_id=str(request.submission_id),
                attachments=[attachment.model_dump(mode="json") for attachment in attachments],
            )
            return MessageAccepted(
                submission_id=str(request.submission_id),
                replayed=result.replayed,
                disposition=cast(Literal["started", "queued"], result.disposition),
                run_id=result.run_id,
                queued=result.queued,
            )
        except SubmissionIdReuseError as exc:
            raise ApiError(409, "submission_id_reused", str(exc)) from exc
        except QueueFullError as exc:
            raise ApiError(409, "session_queue_full", str(exc)) from exc
        except KeyError as exc:
            raise ApiError(404, "session_or_provider_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(409, "session_not_open", str(exc)) from exc
        except PermissionError as exc:
            raise ApiError(409, "working_directory_untrusted", str(exc)) from exc

    @app.get(
        "/v1/sessions/{session_id}/plans/current",
        dependencies=auth,
        response_model=PlanState,
    )
    async def current_plan(session_id: str) -> PlanState:
        try:
            return await state.runs.current_plan(session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.post(
        "/v1/sessions/{session_id}/plans/current/notes",
        dependencies=auth,
        response_model=MessageAccepted,
        status_code=202,
    )
    async def submit_plan_note(session_id: str, request: MessageRequest) -> MessageAccepted:
        try:
            result = await state.runs.submit(
                session_id,
                request.content,
                remember=False,
                paste_spans=[span.model_dump(mode="json") for span in request.paste_spans],
                purpose="plan_note",
                submission_id=str(request.submission_id),
            )
            return MessageAccepted(
                submission_id=str(request.submission_id),
                replayed=result.replayed,
                disposition=cast(Literal["started", "queued"], result.disposition),
                run_id=result.run_id,
                queued=result.queued,
            )
        except SubmissionIdReuseError as exc:
            raise ApiError(409, "submission_id_reused", str(exc)) from exc
        except QueueFullError as exc:
            raise ApiError(409, "session_queue_full", str(exc)) from exc
        except KeyError as exc:
            raise ApiError(404, "session_or_provider_not_found", str(exc)) from exc
        except (PermissionError, ValueError) as exc:
            raise ApiError(409, "plan_note_rejected", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/plans/current/execute",
        dependencies=auth,
        response_model=PlanExecutionAccepted,
        status_code=202,
    )
    async def execute_plan(session_id: str, request: PlanExecuteRequest) -> PlanExecutionAccepted:
        try:
            plan, tasks, run_id = await state.runs.execute_plan(
                session_id, strategy=request.strategy, note=request.note, agent_id=request.agent_id
            )
            return PlanExecutionAccepted(plan=plan, tasks=tasks, run_id=run_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", str(exc)) from exc
        except (ValueError, FileNotFoundError, ProviderError) as exc:
            raise ApiError(409, "plan_execution_rejected", str(exc)) from exc

    @app.get(
        "/v1/sessions/{session_id}/tasks",
        dependencies=auth,
        response_model=SessionTaskList,
    )
    async def session_tasks(session_id: str) -> SessionTaskList:
        try:
            return await state.runs.current_tasks(session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/tasks",
        dependencies=auth,
        response_model=SessionTaskList,
        status_code=201,
    )
    async def add_session_task(session_id: str, request: TaskCreateRequest) -> SessionTaskList:
        try:
            return await state.runs.add_task(session_id, request.text)
        except (KeyError, ValueError) as exc:
            raise ApiError(409, "task_update_rejected", str(exc)) from exc

    @app.patch(
        "/v1/sessions/{session_id}/tasks/{task_id}",
        dependencies=auth,
        response_model=SessionTaskList,
    )
    async def update_session_task(
        session_id: str, task_id: str, request: TaskUpdateRequest
    ) -> SessionTaskList:
        try:
            return await state.runs.update_task(
                session_id,
                task_id,
                text=request.text,
                status=request.status,
                position=request.position,
            )
        except KeyError as exc:
            raise ApiError(404, "task_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(409, "task_update_rejected", str(exc)) from exc

    @app.delete(
        "/v1/sessions/{session_id}/tasks/{task_id}",
        dependencies=auth,
        response_model=SessionTaskList,
    )
    async def remove_session_task(session_id: str, task_id: str) -> SessionTaskList:
        try:
            return await state.runs.remove_task(session_id, task_id)
        except KeyError as exc:
            raise ApiError(404, "task_not_found", str(exc)) from exc

    @app.post("/v1/sessions/{session_id}/dream", dependencies=auth, status_code=202)
    async def dream_session(session_id: str) -> dict[str, str]:
        try:
            return {"dream_id": await state.runs.dream(session_id)}
        except KeyError as exc:
            raise ApiError(404, "session_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(409, "session_not_dreamable", str(exc)) from exc
        except PermissionError as exc:
            raise ApiError(409, "working_directory_untrusted", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/compact",
        dependencies=auth,
        response_model=CompactionAccepted,
        status_code=202,
    )
    async def compact_session(session_id: str) -> CompactionAccepted:
        try:
            return CompactionAccepted(run_id=await state.runs.compact(session_id))
        except KeyError as exc:
            raise ApiError(404, "session_or_provider_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(409, "session_not_compactable", str(exc)) from exc
        except PermissionError as exc:
            raise ApiError(409, "working_directory_untrusted", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/goals",
        dependencies=auth,
        response_model=Goal,
        status_code=202,
    )
    async def create_goal(session_id: str, request: GoalCreateRequest) -> Goal:
        try:
            return await state.runs.start_goal(session_id, request.objective)
        except KeyError as exc:
            raise ApiError(404, "session_or_provider_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(409, "goal_conflict", str(exc)) from exc
        except PermissionError as exc:
            raise ApiError(409, "working_directory_untrusted", str(exc)) from exc

    @app.get(
        "/v1/sessions/{session_id}/goals/current",
        dependencies=auth,
        response_model=Goal | None,
    )
    async def current_goal(session_id: str) -> Goal | None:
        try:
            return await state.runs.current_goal(session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", str(exc)) from exc

    @app.get(
        "/v1/sessions/{session_id}/goals",
        dependencies=auth,
        response_model=list[Goal],
    )
    async def goal_history(session_id: str) -> list[Goal]:
        try:
            return await state.runs.goal_history(session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/goals/current/pause",
        dependencies=auth,
        response_model=Goal,
    )
    async def pause_goal(session_id: str) -> Goal:
        try:
            return await state.runs.pause_goal(session_id)
        except (KeyError, ValueError) as exc:
            raise ApiError(409, "goal_not_pausable", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/goals/current/resume",
        dependencies=auth,
        response_model=Goal,
        status_code=202,
    )
    async def resume_goal(session_id: str) -> Goal:
        try:
            return await state.runs.resume_goal(session_id)
        except KeyError as exc:
            raise ApiError(404, "session_or_provider_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(409, "goal_not_resumable", str(exc)) from exc
        except PermissionError as exc:
            raise ApiError(409, "working_directory_untrusted", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/goals/current/cancel",
        dependencies=auth,
        response_model=Goal,
    )
    async def cancel_goal(session_id: str) -> Goal:
        try:
            return await state.runs.cancel_goal(session_id)
        except (KeyError, ValueError) as exc:
            raise ApiError(409, "goal_not_cancellable", str(exc)) from exc

    @app.get("/v1/sessions/{session_id}/queue", dependencies=auth, response_model=QueueState)
    async def queue_state(session_id: str) -> QueueState:
        try:
            return await state.runs.queue_state(session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.post(
        "/v1/sessions/{session_id}/queue/take-latest",
        dependencies=auth,
        response_model=QueuedMessage,
    )
    async def take_latest_queued(session_id: str) -> QueuedMessage:
        try:
            return await state.runs.take_latest_queued(session_id)
        except KeyError as exc:
            raise ApiError(404, "queue_empty", "the session queue is empty") from exc

    @app.post(
        "/v1/sessions/{session_id}/queue/{queue_id}/take",
        dependencies=auth,
        response_model=QueuedMessage,
    )
    async def take_queued(session_id: str, queue_id: str) -> QueuedMessage:
        try:
            return await state.runs.take_queued(session_id, queue_id)
        except KeyError as exc:
            raise ApiError(
                404, "queued_message_not_found", f"unknown queue item: {queue_id}"
            ) from exc

    @app.post(
        "/v1/sessions/{session_id}/queue/{queue_id}/send-now",
        dependencies=auth,
        response_model=MessageAccepted,
        status_code=202,
    )
    async def send_queued_now(session_id: str, queue_id: str) -> MessageAccepted:
        try:
            result = await state.runs.send_queued_now(session_id, queue_id)
            return MessageAccepted(
                submission_id=queue_id,
                disposition=cast(Literal["started", "queued"], result.disposition),
                run_id=result.run_id,
                queued=result.queued,
            )
        except KeyError as exc:
            raise ApiError(
                404, "queued_message_not_found", f"unknown queue item: {queue_id}"
            ) from exc
        except ValueError as exc:
            raise ApiError(409, "queued_message_not_sendable", str(exc)) from exc

    @app.patch(
        "/v1/sessions/{session_id}/queue/{queue_id}",
        dependencies=auth,
        response_model=QueueState,
    )
    async def edit_queued(
        session_id: str, queue_id: str, request: QueuedMessageEditRequest
    ) -> QueueState:
        try:
            return await state.runs.edit_queued(
                session_id,
                queue_id,
                content=request.content,
                expected_content=request.expected_content,
            )
        except KeyError as exc:
            raise ApiError(
                404,
                "queued_message_not_found",
                "This message already started or was removed. Your edit was not sent.",
            ) from exc
        except ValueError as exc:
            raise ApiError(409, "queued_message_edit_conflict", str(exc)) from exc

    @app.delete(
        "/v1/sessions/{session_id}/queue/{queue_id}",
        dependencies=auth,
        response_model=QueueState,
    )
    async def delete_queued(session_id: str, queue_id: str) -> QueueState:
        try:
            return await state.runs.delete_queued(session_id, queue_id)
        except KeyError as exc:
            raise ApiError(
                404, "queued_message_not_found", f"unknown queue item: {queue_id}"
            ) from exc

    @app.delete("/v1/sessions/{session_id}/queue", dependencies=auth, response_model=QueueState)
    async def clear_queue(session_id: str) -> QueueState:
        try:
            return await state.runs.clear_queue(session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.post("/v1/sessions/{session_id}/queue/pause", dependencies=auth, response_model=QueueState)
    async def pause_queue(session_id: str) -> QueueState:
        try:
            return await state.runs.pause_queue(session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.post(
        "/v1/sessions/{session_id}/queue/resume", dependencies=auth, response_model=QueueState
    )
    async def resume_queue(session_id: str) -> QueueState:
        try:
            return await state.runs.resume_queue(session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.post("/v1/runs/{run_id}/cancel", dependencies=auth)
    async def cancel_run(run_id: str) -> dict[str, bool]:
        if not await state.runs.cancel(run_id):
            raise ApiError(404, "run_not_active", f"run is not active: {run_id}")
        return {"cancelled": True}

    @app.post(
        "/v1/approvals/{approval_id}",
        dependencies=auth,
        response_model=ApprovalResolution,
    )
    async def resolve_approval(
        approval_id: str, request: ApprovalDecisionRequest
    ) -> ApprovalResolution:
        try:
            approval = await state.runs.resolve_approval(
                approval_id,
                request_hash=request.request_hash,
                decision=request.decision,
            )
        except KeyError as exc:
            raise ApiError(404, "approval_not_found", f"unknown approval: {approval_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "approval_hash_mismatch", str(exc)) from exc
        except RuntimeError as exc:
            raise ApiError(409, "approval_not_pending", str(exc)) from exc
        return ApprovalResolution(
            approval_id=approval.id,
            request_hash=approval.request_hash,
            status=approval.status,
            approval_scope=approval.approval_scope,
        )

    @app.post(
        "/v1/questions/{question_id}",
        dependencies=auth,
        response_model=QuestionResolution,
    )
    async def resolve_question(
        question_id: str, request: QuestionAnswerRequest
    ) -> QuestionResolution:
        try:
            answer = await state.runs.resolve_question(
                question_id,
                selected_option=request.selected_option,
                selected_options=request.selected_options,
                note=request.note,
                custom_answer=request.custom_answer,
            )
        except ValueError as exc:
            raise ApiError(422, "question_answer_invalid", str(exc)) from exc
        except RuntimeError as exc:
            raise ApiError(409, "question_not_pending", str(exc)) from exc
        return QuestionResolution(
            question_id=question_id,
            answer=answer.answer,
            answer_type=answer.answer_type,
            selected_option=answer.selected_option,
            selected_description=answer.selected_description,
            selected_options=list(answer.selected_options),
            selected_descriptions=list(answer.selected_descriptions),
            note=answer.note,
            custom=answer.custom,
        )

    @app.get("/v1/sessions/{session_id}/commands", dependencies=auth)
    async def user_commands(session_id: str) -> list[dict[str, object]]:
        from hames.commands import load_commands

        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            commands = await asyncio.to_thread(
                load_commands, state.paths.root, Path(session.working_directory)
            )
            return [command.model_dump() for command in commands]
        except KeyError as exc:
            raise ApiError(404, "session_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(400, "invalid_command_config", str(exc)) from exc

    @app.post(
        "/v1/sessions/{session_id}/commands/{name}",
        dependencies=auth,
        response_model=PlanExecutionAccepted,
        status_code=202,
    )
    async def invoke_user_command(
        session_id: str, name: str, request: Request
    ) -> PlanExecutionAccepted:
        from hames.commands import load_commands

        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            commands = await asyncio.to_thread(
                load_commands, state.paths.root, Path(session.working_directory)
            )
            command = next((item for item in commands if item.name == name), None)
            if command is None:
                raise ApiError(404, "command_not_found", f"Unknown command: /{name}")
            body = await request.json()
            note = cast(dict[str, object], body).get("note", "") if isinstance(body, dict) else None
            if not isinstance(note, str):
                raise ValueError("Command note must be text")
            agent = await asyncio.to_thread(state.agents.load, command.agent)
            plan, tasks, run_id = await state.runs.execute_plan(
                session_id, strategy="keep", note=note, agent_id=agent.metadata.id
            )
            return PlanExecutionAccepted(plan=plan, tasks=tasks, run_id=run_id)
        except (KeyError, FileNotFoundError) as exc:
            raise ApiError(404, "command_target_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(400, "command_rejected", str(exc)) from exc
        except ProviderError as exc:
            raise ApiError(400, "command_provider_unavailable", str(exc)) from exc

    @app.get("/v1/events", dependencies=auth)
    async def stream_events(
        request: Request,
        session_id: str,
        after_sequence: Annotated[int | None, Query(ge=0)] = None,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        resume_after = _resume_sequence(after_sequence, last_event_id)

        async def generate() -> AsyncIterator[str]:
            async with state.broker.subscribe(session_id, include_snapshot=True) as queue:
                # Send headers only after the subscriber is registered.  Clients can
                # now safely open the stream before starting a run without either
                # side waiting for the other or losing the first transient delta.
                yield ": connected\n\n"
                # Snapshot and subscription share a lock: all subsequent deltas are queued.
                # Its watermark lets clients replay older durable messages without erasing
                # the current response, even across several model turns in the same run.
                yield _sse(queue.get_nowait())
                replay = await asyncio.to_thread(
                    state.ledger.list_events, session_id, after_sequence=resume_after
                )
                # Events published while the ledger was read must retain their queue
                # ordering with transient deltas, rather than being replayed ahead of them.
                buffered: list[dict[str, object]] = []
                while not queue.empty():
                    buffered.append(queue.get_nowait())
                buffered_ids: set[str] = set()
                for item in buffered:
                    value = item.get("event")
                    if isinstance(value, dict):
                        identifier = cast(dict[str, object], value).get("id")
                        if isinstance(identifier, str):
                            buffered_ids.add(identifier)
                seen = resume_after
                for event in replay:
                    if event.id in buffered_ids:
                        continue
                    seen = max(seen, event.sequence)
                    yield _sse({"durable": True, "event": event.model_dump(mode="json")})
                while not await request.is_disconnected():
                    try:
                        item = (
                            buffered.pop(0)
                            if buffered
                            else await asyncio.wait_for(queue.get(), timeout=15)
                        )
                    except TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    event_value: object = item.get("event")
                    if isinstance(event_value, dict):
                        event_data = cast(dict[str, object], event_value)
                        sequence = event_data.get("sequence")
                        if isinstance(sequence, int) and sequence <= seen:
                            continue
                        if isinstance(sequence, int):
                            seen = sequence
                    yield _sse(item)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    install_web_routes(app, state.web, bearer_dependencies=bearer_auth)
    return app


def _sse(item: dict[str, object]) -> str:
    event_value: object = item.get("event")
    event_id = ""
    event_type = str(item.get("type", "event"))
    if isinstance(event_value, dict):
        event_data = cast(dict[str, object], event_value)
        if isinstance(event_data.get("sequence"), int):
            event_id = f"id: {event_data['sequence']}\n"
        event_type = str(event_data.get("type", "event"))
    return f"{event_id}event: {event_type}\ndata: {json.dumps(item, separators=(',', ':'))}\n\n"


def _resume_sequence(after_sequence: int | None, last_event_id: str | None) -> int:
    if last_event_id is None:
        return after_sequence or 0
    try:
        header_sequence = int(last_event_id)
    except ValueError as exc:
        raise ApiError(400, "invalid_event_cursor", "Last-Event-ID must be an integer") from exc
    if header_sequence < 0:
        raise ApiError(400, "invalid_event_cursor", "Last-Event-ID must not be negative")
    if after_sequence is not None and after_sequence != header_sequence:
        raise ApiError(
            400,
            "conflicting_event_cursor",
            "Last-Event-ID and after_sequence must match when both are supplied",
        )
    return header_sequence


def _public_profile(state: GatewayState, profile_id: str, provider: Provider) -> ProviderProfile:
    configured = state.provider_profile(profile_id)
    is_default = profile_id == state.config.runtime.default_provider
    return ProviderProfile(
        id=profile_id,
        adapter=configured.adapter if configured else provider.adapter,
        endpoint=configured.base_url if configured else provider.base_url,
        configured_model=(
            state.config.runtime.default_model
            if is_default and state.config.runtime.default_model
            else configured.model
            if configured
            else ""
        ),
        default_reasoning_effort=(
            state.config.runtime.default_reasoning_effort
            if is_default
            else configured.reasoning_effort
            if configured
            else ""
        ),
        supported_reasoning_efforts=(configured.supported_reasoning_efforts if configured else []),
    )


def _agent_public(agent: AgentSummary) -> AgentPublic:
    return AgentPublic(
        id=agent.id,
        slug=agent.slug or agent.id,
        name=agent.name,
        authority=agent.authority,
        path=str(agent.path),
        content_hash=agent.content_hash,
        avatar=agent.avatar,
    )


def _agent_detail(capsule: AgentCapsule) -> AgentDetail:
    return AgentDetail(
        **_agent_public(
            AgentSummary(
                id=capsule.metadata.id,
                slug=capsule.metadata.slug or capsule.metadata.id,
                name=capsule.metadata.name,
                authority=capsule.metadata.authority,
                path=capsule.path,
                content_hash=capsule.content_hash,
                avatar=capsule.metadata.avatar,
            )
        ).model_dump(),
        default_model=capsule.metadata.execution,
        source=capsule.path.read_text(encoding="utf-8"),
        instructions=capsule.instructions,
        tools_allow=capsule.metadata.tools.allow,
        tools_deny=capsule.metadata.tools.deny,
        skills_allow=capsule.metadata.skills.allow,
        skills_deny=capsule.metadata.skills.deny,
        skills_pin=capsule.metadata.skills.pin,
        delegation_allowed=capsule.metadata.delegation.allow,
        delegation_targets=capsule.metadata.delegation.allowed_agents,
        deprecated_fields=capsule.deprecated_fields,
    )


async def _probe(profile_id: str, provider: Provider) -> ProviderProbe:
    try:
        models = await provider.list_models()
        return ProviderProbe(
            id=profile_id,
            adapter=provider.adapter,
            reachable=True,
            models=models,
        )
    except ProviderError as exc:
        return ProviderProbe(
            id=profile_id,
            adapter=provider.adapter,
            reachable=False,
            models=[],
            error=ProviderProbeError(
                code=exc.code,
                message=str(exc),
                retryable=exc.retryable,
                details=dict(exc.details),
            ),
        )


def _plugin_upload_root(paths: HamesPaths, upload_id: str) -> Path:
    if len(upload_id) != 32 or any(character not in "0123456789abcdef" for character in upload_id):
        raise ApiError(404, "plugin_upload_not_found", "unknown plugin upload")
    root = paths.plugin_uploads / upload_id
    if not root.is_dir():
        raise ApiError(404, "plugin_upload_not_found", "unknown plugin upload")
    return root


def _write_plugin_upload(root: Path, files: list[PluginUploadFile]) -> None:
    total = 0
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    for uploaded in files:
        relative = PurePosixPath(uploaded.path.replace("\\", "/"))
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part in {"", ".", ".."} for part in relative.parts)
        ):
            raise ValueError(f"unsafe plugin upload path: {uploaded.path}")
        encoded = base64.b64decode(uploaded.data_base64, validate=True)
        total += len(encoded)
        if len(encoded) > 12_000_000 or total > 24_000_000:
            raise ValueError("plugin upload exceeds the 24 MB package limit")
        destination = root.joinpath(*relative.parts)
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination.write_bytes(encoded)
        destination.chmod(0o600)
