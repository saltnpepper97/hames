"""Versioned local HTTP/SSE gateway."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal, cast

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from hames import PROTOCOL_VERSION, __version__
from hames.agent import (
    AgentCapsule,
    AgentRegistry,
    AgentSummary,
    apply_agent_skill_policy,
    load_agent,
    skill_permitted,
)
from hames.broker import EventBroker
from hames.config import HamesConfig, ProviderProfileConfig, load_config
from hames.control import ControlStore
from hames.database import Database
from hames.evolution import Scar, ScarStatus, ScarStore
from hames.evolution_runtime import EvolutionManager
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
    session_runs,
    session_usage,
)
from hames.ledger import Event, EventIntegrityError, IntegrityResult, Ledger, Session
from hames.memory import (
    MemoryJob,
    MemoryLayer,
    MemoryRecord,
    MemoryStatus,
    MemoryVisibility,
    contains_secret,
)
from hames.memory_runtime import MemoryManager
from hames.message_queue import QueuedMessage, QueueFullError, QueueState
from hames.paths import HamesPaths
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
from hames.providers.scheduled import SerializedProvider
from hames.rules import (
    ContextRule,
    ContextRuleCondition,
    PolicyRule,
)
from hames.runtime import RunManager
from hames.skill_runtime import SkillManager
from hames.skills import SkillJob, SkillSummary, SkillVersion


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSessionRequest(ApiModel):
    working_directory: str
    agent_id: str = "default"
    provider: str = ""
    model: str = ""
    reasoning_effort: str = ""
    title: str | None = None
    inherit_session_id: str | None = None


class PasteSpan(ApiModel):
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)
    line_count: int = Field(ge=1)
    byte_count: int = Field(ge=1)


def _empty_paste_spans() -> list[PasteSpan]:
    return []


class MessageRequest(ApiModel):
    content: str = Field(min_length=1)
    remember: bool = False
    send_now: bool = False
    paste_spans: list[PasteSpan] = Field(default_factory=_empty_paste_spans, max_length=64)

    @model_validator(mode="after")
    def validate_paste_spans(self) -> MessageRequest:
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


class MessageAccepted(ApiModel):
    disposition: Literal["started", "queued"]
    run_id: str | None = None
    queued: QueuedMessage | None = None


class CompactionAccepted(ApiModel):
    run_id: str
    trigger: Literal["manual"] = "manual"


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


class ForkSessionRequest(ApiModel):
    at: str | None = None
    title: str | None = None
    agent_id: str | None = None


class AgentCreateRequest(ApiModel):
    name: str | None = Field(default=None, max_length=80)
    authority: Literal["standard", "read_only"] = "standard"
    source: str | None = Field(default=None, max_length=65_536)


class MemoryCaptureRequest(ApiModel):
    content: str = Field(min_length=1, max_length=32_000)


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


class SkillAuthorRequest(ApiModel):
    goal: str = Field(min_length=1, max_length=4000)
    scope: Literal["workspace", "agent"] = "workspace"
    target_skill_id: str | None = None


class PluginPathRequest(ApiModel):
    path: str = Field(min_length=1, max_length=1024)


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
    id: str
    name: str
    authority: str
    path: str
    content_hash: str


class AgentDetail(AgentPublic):
    instructions: str
    tools_allow: list[str]
    tools_deny: list[str]
    skills_allow: list[str]
    skills_deny: list[str]
    skills_pin: list[str]
    delegation_allowed: bool
    delegation_targets: list[str]
    deprecated_fields: list[str]


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


@dataclass(slots=True)
class GatewayState:
    paths: HamesPaths
    config: HamesConfig
    ledger: Ledger
    controls: ControlStore
    providers: dict[str, Provider]
    broker: EventBroker
    runs: RunManager
    memory: MemoryManager
    skills: SkillManager
    evolution: EvolutionManager
    plugins: PluginManager
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
        controls = ControlStore(database)
        broker = EventBroker()
        raw_providers = providers or configured_providers(config)
        selected_providers: dict[str, Provider] = {
            profile_id: SerializedProvider(provider)
            for profile_id, provider in raw_providers.items()
        }
        runs = RunManager(
            ledger=ledger,
            paths=paths,
            config=config,
            controls=controls,
            providers=selected_providers,
            broker=broker,
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
            controls,
            selected_providers,
            broker,
            runs,
            memory,
            skills,
            evolution,
            plugins,
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
        await state.plugins.start_enabled()
        await state.runs.recover_queues()
        yield
        await state.runs.close()

    app = FastAPI(title="Hames Gateway", version=__version__, lifespan=lifespan)

    async def authenticate(authorization: Annotated[str | None, Header()] = None) -> None:
        if authorization != f"Bearer {state.token}":
            raise ApiError(401, "unauthorized", "a valid local gateway token is required")

    auth = [Depends(authenticate)]

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
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "request validation failed",
                    "retryable": False,
                    "details": {"errors": json.loads(json.dumps(exc.errors(), default=str))},
                }
            },
        )

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
        return Health(
            status="ok",
            version=__version__,
            protocol_version=PROTOCOL_VERSION,
            database_ready=state.paths.database.exists(),
            provider_profiles=sorted(state.providers),
            default_provider=state.config.runtime.default_provider,
            active_runs=state.runs.active_run_count,
        )

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
        profile_id: str, requested_model: str, requested_effort: str
    ) -> tuple[str, str, int, str]:
        provider = state.providers.get(profile_id)
        if provider is None:
            raise ApiError(400, "unknown_provider", f"unknown provider: {profile_id}")
        configured = state.provider_profile(profile_id)
        selected_model_id = requested_model or (configured.model if configured else "")
        selected_effort = requested_effort or (configured.reasoning_effort if configured else "")
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
        agent_id = inherited.agent_id if inherited is not None else request.agent_id
        try:
            await asyncio.to_thread(state.agents.load, agent_id)
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(400, "unknown_agent", str(exc)) from exc
        if inherited is None:
            provider_name = request.provider or state.config.runtime.default_provider
            selection = await resolve_selection(
                provider_name, request.model, request.reasoning_effort
            )
            model, reasoning_effort, context_window_tokens, context_window_source = selection
            interaction_mode = "auto"
        else:
            provider_name = inherited.provider
            model = inherited.model
            reasoning_effort = inherited.reasoning_effort
            context_window_tokens = inherited.context_window_tokens
            context_window_source = inherited.context_window_source
            interaction_mode = inherited.interaction_mode
        try:
            return await asyncio.to_thread(
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
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(400, "invalid_working_directory", str(exc)) from exc

    @app.get("/v1/sessions", dependencies=auth, response_model=list[Session])
    async def list_sessions() -> list[Session]:
        return await asyncio.to_thread(state.ledger.list_sessions)

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
        return sorted(state.runs.tools.names() | state.plugins.names())

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

    @app.post("/v1/agents/{agent_id}/validate", dependencies=auth, response_model=AgentDetail)
    async def validate_agent(agent_id: str) -> AgentDetail:
        return await get_agent(agent_id)

    @app.get("/v1/agents/{agent_id}/usage", dependencies=auth, response_model=AgentUsageProjection)
    async def get_agent_usage(agent_id: str) -> AgentUsageProjection:
        try:
            await asyncio.to_thread(state.agents.load, agent_id)
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
            return await asyncio.to_thread(state.ledger.get_session, session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

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
            await asyncio.to_thread(state.agents.load, request.agent_id)
            return await asyncio.to_thread(
                state.ledger.update_session_agent, session_id, agent_id=request.agent_id
            )
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(400, "unknown_agent", str(exc)) from exc

    @app.put("/v1/sessions/{session_id}/mode", dependencies=auth, response_model=Session)
    async def update_session_mode(session_id: str, request: UpdateSessionModeRequest) -> Session:
        try:
            return await asyncio.to_thread(
                state.ledger.update_session_mode, session_id, mode=request.mode
            )
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

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
            return await state.memory.enqueue_capture(session, request.content, source)
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
            return await state.memory.retry(session_id, job_id)
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
            capsule = await asyncio.to_thread(
                load_agent, state.paths.agents / session.agent_id / "AGENT.md"
            )
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
            return await asyncio.to_thread(state.runs.skills.visible, session, query="", limit=200)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

    @app.get(
        "/v1/sessions/{session_id}/skills/{slug}",
        dependencies=auth,
        response_model=SkillVersion,
    )
    async def get_skill(session_id: str, slug: str) -> SkillVersion:
        try:
            session = await asyncio.to_thread(state.ledger.get_session, session_id)
            capsule = await asyncio.to_thread(
                load_agent, state.paths.agents / session.agent_id / "AGENT.md"
            )
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
            return await state.skills.author(
                session,
                goal=request.goal,
                scope=request.scope,
                target_skill_id=request.target_skill_id,
            )
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
            return await state.skills.retry(session_id, job_id)
        except KeyError as exc:
            raise ApiError(404, "skill_job_not_found", f"unknown Skill job: {job_id}") from exc
        except ValueError as exc:
            raise ApiError(409, "skill_job_not_retryable", str(exc)) from exc

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
            return await asyncio.to_thread(session_usage, state.ledger, session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

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
            result = await state.runs.submit(
                session_id,
                request.content,
                remember=request.remember,
                paste_spans=[span.model_dump(mode="json") for span in request.paste_spans],
                send_now=request.send_now,
            )
            return MessageAccepted(
                disposition=cast(Literal["started", "queued"], result.disposition),
                run_id=result.run_id,
                queued=result.queued,
            )
        except QueueFullError as exc:
            raise ApiError(409, "session_queue_full", str(exc)) from exc
        except KeyError as exc:
            raise ApiError(404, "session_or_provider_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(409, "session_not_open", str(exc)) from exc
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

    @app.get("/v1/events", dependencies=auth)
    async def stream_events(
        request: Request,
        session_id: str,
        after_sequence: Annotated[int | None, Query(ge=0)] = None,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        resume_after = _resume_sequence(after_sequence, last_event_id)

        async def generate() -> AsyncIterator[str]:
            async with state.broker.subscribe(session_id) as queue:
                # Send headers only after the subscriber is registered.  Clients can
                # now safely open the stream before starting a run without either
                # side waiting for the other or losing the first transient delta.
                yield ": connected\n\n"
                replay = await asyncio.to_thread(
                    state.ledger.list_events, session_id, after_sequence=resume_after
                )
                seen = resume_after
                for event in replay:
                    seen = max(seen, event.sequence)
                    yield _sse({"durable": True, "event": event.model_dump(mode="json")})
                while not await request.is_disconnected():
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=15)
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
    return ProviderProfile(
        id=profile_id,
        adapter=configured.adapter if configured else provider.adapter,
        endpoint=configured.base_url if configured else provider.base_url,
        configured_model=configured.model if configured else "",
        default_reasoning_effort=configured.reasoning_effort if configured else "",
        supported_reasoning_efforts=(configured.supported_reasoning_efforts if configured else []),
    )


def _agent_public(agent: AgentSummary) -> AgentPublic:
    return AgentPublic(
        id=agent.id,
        name=agent.name,
        authority=agent.authority,
        path=str(agent.path),
        content_hash=agent.content_hash,
    )


def _agent_detail(capsule: AgentCapsule) -> AgentDetail:
    return AgentDetail(
        **_agent_public(
            AgentSummary(
                id=capsule.metadata.id,
                name=capsule.metadata.name,
                authority=capsule.metadata.authority,
                path=capsule.path,
                content_hash=capsule.content_hash,
            )
        ).model_dump(),
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
