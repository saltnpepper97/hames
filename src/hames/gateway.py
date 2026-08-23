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
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from hames import PROTOCOL_VERSION, __version__
from hames.broker import EventBroker
from hames.config import HamesConfig, ProviderProfileConfig, load_config
from hames.control import ControlStore
from hames.database import Database
from hames.ledger import Event, EventIntegrityError, IntegrityResult, Ledger, Session
from hames.paths import HamesPaths
from hames.providers import Provider, ProviderError, ProviderModel
from hames.providers.registry import configured_providers
from hames.runtime import RunManager


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateSessionRequest(ApiModel):
    working_directory: str
    agent_id: str = "default"
    provider: str = ""
    model: str = ""
    reasoning_effort: str = ""
    title: str | None = None


class MessageRequest(ApiModel):
    content: str = Field(min_length=1)


class UpdateSessionRequest(ApiModel):
    provider: str
    model: str
    reasoning_effort: str = ""


class RunAccepted(ApiModel):
    run_id: str


class TrustStatus(ApiModel):
    path: str
    trusted: bool
    grant_id: str | None = None
    created_at: str | None = None


class ApprovalDecisionRequest(ApiModel):
    decision: Literal["approved", "denied"]
    request_hash: str = Field(min_length=64, max_length=64)


class ApprovalResolution(ApiModel):
    approval_id: str
    request_hash: str
    status: str


class ForkSessionRequest(ApiModel):
    at: str | None = None
    title: str | None = None


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
        selected_providers = providers or configured_providers(config)
        runs = RunManager(
            ledger=ledger,
            paths=paths,
            config=config,
            controls=controls,
            providers=selected_providers,
            broker=broker,
        )
        return cls(
            paths,
            config,
            ledger,
            controls,
            selected_providers,
            broker,
            runs,
            paths.read_gateway_token(),
        )

    def provider_profile(self, profile_id: str) -> ProviderProfileConfig | None:
        return self.config.providers.get(profile_id)


def create_app(state: GatewayState) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
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
    ) -> tuple[str, str]:
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
        efforts = (
            configured.supported_reasoning_efforts
            if configured and configured.supported_reasoning_efforts
            else selected.reasoning_efforts
        )
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
        return selected_model_id, selected_effort

    @app.post("/v1/sessions", dependencies=auth, response_model=Session, status_code=201)
    async def create_session(request: CreateSessionRequest) -> Session:
        provider_name = request.provider or state.config.runtime.default_provider
        model, reasoning_effort = await resolve_selection(
            provider_name, request.model, request.reasoning_effort
        )
        try:
            return await asyncio.to_thread(
                state.ledger.create_session,
                working_directory=Path(request.working_directory),
                agent_id=request.agent_id,
                provider=provider_name,
                model=model,
                reasoning_effort=reasoning_effort,
                title=request.title,
            )
        except (FileNotFoundError, ValueError) as exc:
            raise ApiError(400, "invalid_working_directory", str(exc)) from exc

    @app.get("/v1/sessions", dependencies=auth, response_model=list[Session])
    async def list_sessions() -> list[Session]:
        return await asyncio.to_thread(state.ledger.list_sessions)

    @app.get("/v1/sessions/{session_id}", dependencies=auth, response_model=Session)
    async def get_session(session_id: str) -> Session:
        try:
            return await asyncio.to_thread(state.ledger.get_session, session_id)
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

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
        if state.runs.is_session_active(session_id):
            raise ApiError(409, "session_run_active", "cannot revoke trust during an active run")
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
        model, reasoning_effort = await resolve_selection(
            request.provider, request.model, request.reasoning_effort
        )
        try:
            return await asyncio.to_thread(
                state.ledger.update_session_settings,
                session_id,
                provider=request.provider,
                model=model,
                reasoning_effort=reasoning_effort,
            )
        except KeyError as exc:
            raise ApiError(404, "session_not_found", f"unknown session: {session_id}") from exc

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
            return await asyncio.to_thread(
                state.ledger.fork_session,
                session_id,
                fork_event_id=fork_event_id,
                title=request.title,
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
        response_model=RunAccepted,
        status_code=202,
    )
    async def send_message(session_id: str, request: MessageRequest) -> RunAccepted:
        try:
            return RunAccepted(run_id=await state.runs.start(session_id, request.content))
        except KeyError as exc:
            raise ApiError(404, "session_or_provider_not_found", str(exc)) from exc
        except ValueError as exc:
            raise ApiError(409, "session_not_open", str(exc)) from exc
        except PermissionError as exc:
            raise ApiError(409, "working_directory_untrusted", str(exc)) from exc

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
