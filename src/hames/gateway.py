"""Versioned local HTTP/SSE gateway."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, cast

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from hames import PROTOCOL_VERSION, __version__
from hames.broker import EventBroker
from hames.config import HamesConfig, load_config
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


class ForkSessionRequest(ApiModel):
    at: str | None = None
    title: str | None = None


class ProviderStatus(ApiModel):
    id: str
    available: bool
    models: list[ProviderModel]
    error: str | None = None


class Health(ApiModel):
    status: str
    version: str
    protocol_version: int
    database_ready: bool


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
        broker = EventBroker()
        selected_providers = providers or configured_providers(config)
        runs = RunManager(
            ledger=ledger,
            paths=paths,
            providers=selected_providers,
            broker=broker,
        )
        return cls(
            paths, config, ledger, selected_providers, broker, runs, paths.read_gateway_token()
        )


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
        )

    @app.get("/v1/providers", dependencies=auth, response_model=list[ProviderStatus])
    async def providers_endpoint() -> list[ProviderStatus]:
        result: list[ProviderStatus] = []
        for name, provider in state.providers.items():
            try:
                models = await provider.list_models()
                result.append(ProviderStatus(id=name, available=True, models=models))
            except ProviderError as exc:
                result.append(ProviderStatus(id=name, available=False, models=[], error=str(exc)))
        return result

    @app.post("/v1/sessions", dependencies=auth, response_model=Session, status_code=201)
    async def create_session(request: CreateSessionRequest) -> Session:
        provider_name = request.provider or state.config.runtime.default_provider
        provider = state.providers.get(provider_name)
        if provider is None:
            raise ApiError(400, "unknown_provider", f"unknown provider: {provider_name}")
        model = request.model
        if not model:
            try:
                models = await provider.list_models()
            except ProviderError as exc:
                raise ApiError(503, exc.code, str(exc), retryable=exc.retryable) from exc
            if len(models) != 1:
                raise ApiError(
                    409,
                    "model_selection_required",
                    "provider did not report exactly one model",
                    details={"models": [item.id for item in models]},
                )
            model = models[0].id
        try:
            return await asyncio.to_thread(
                state.ledger.create_session,
                working_directory=Path(request.working_directory),
                agent_id=request.agent_id,
                provider=provider_name,
                model=model,
                reasoning_effort=request.reasoning_effort,
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

    @app.patch("/v1/sessions/{session_id}", dependencies=auth, response_model=Session)
    async def update_session(session_id: str, request: UpdateSessionRequest) -> Session:
        provider = state.providers.get(request.provider)
        if provider is None:
            raise ApiError(400, "unknown_provider", f"unknown provider: {request.provider}")
        models = await provider.list_models()
        selected = next((item for item in models if item.id == request.model), None)
        if selected is None:
            raise ApiError(400, "unknown_model", f"unknown model: {request.model}")
        if request.reasoning_effort and request.reasoning_effort != "off":
            if selected.reasoning_supported is False:
                raise ApiError(400, "reasoning_not_supported", "model does not advertise reasoning")
            if (
                selected.reasoning_efforts
                and request.reasoning_effort not in selected.reasoning_efforts
            ):
                raise ApiError(
                    400,
                    "reasoning_effort_not_supported",
                    f"unsupported reasoning effort: {request.reasoning_effort}",
                    details={"supported": selected.reasoning_efforts},
                )
        try:
            return await asyncio.to_thread(
                state.ledger.update_session_settings,
                session_id,
                provider=request.provider,
                model=request.model,
                reasoning_effort=request.reasoning_effort,
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

    @app.post("/v1/runs/{run_id}/cancel", dependencies=auth)
    async def cancel_run(run_id: str) -> dict[str, bool]:
        if not await state.runs.cancel(run_id):
            raise ApiError(404, "run_not_active", f"run is not active: {run_id}")
        return {"cancelled": True}

    @app.get("/v1/events", dependencies=auth)
    async def stream_events(
        request: Request,
        session_id: str,
        after_sequence: Annotated[int, Query(ge=0)] = 0,
    ) -> StreamingResponse:
        async def generate() -> AsyncIterator[str]:
            async with state.broker.subscribe(session_id) as queue:
                # Send headers only after the subscriber is registered.  Clients can
                # now safely open the stream before starting a run without either
                # side waiting for the other or losing the first transient delta.
                yield ": connected\n\n"
                replay = await asyncio.to_thread(
                    state.ledger.list_events, session_id, after_sequence=after_sequence
                )
                seen = after_sequence
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

        return StreamingResponse(generate(), media_type="text/event-stream")

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
