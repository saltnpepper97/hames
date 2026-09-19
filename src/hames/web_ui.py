"""Persistent gateway-owned web shell and browser authentication."""

# pyright: reportUnusedFunction=false

from __future__ import annotations

import hmac
import mimetypes
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.params import Depends as DependsParameter
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from hames import PROTOCOL_VERSION
from hames.config import GatewayConfig

WEB_PROTOCOL_VERSION = 1
WEB_SESSION_COOKIE = "hames_web_session"
WEB_CSRF_HEADER = "x-hames-csrf"
LAUNCH_LIFETIME_SECONDS = 60.0
MAX_PENDING_LAUNCHES = 32
MAX_BROWSER_SESSIONS = 64


class WebApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WebLaunchRequest(WebApiModel):
    working_directory: str = Field(min_length=1, max_length=4096)


class WebLaunchResponse(WebApiModel):
    url: str


class WebBootstrap(WebApiModel):
    protocol_version: int
    gateway_protocol_version: int
    working_directory: str
    csrf_token: str


class WebUiError(RuntimeError):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


@dataclass(frozen=True, slots=True)
class PendingLaunch:
    working_directory: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class BrowserSession:
    working_directory: str
    csrf_token: str


class WebUi:
    """Own process-local browser sessions for the persistent gateway."""

    def __init__(
        self,
        gateway: GatewayConfig,
        *,
        asset_root: Path | None = None,
    ) -> None:
        host = "127.0.0.1" if gateway.host == "localhost" else gateway.host
        authority_host = f"[{host}]" if ":" in host else host
        self.authority = f"{authority_host}:{gateway.port}"
        self.origin = f"http://{self.authority}"
        self.asset_root = (asset_root or Path(__file__).with_name("web_dist")).resolve()
        self._launches: dict[str, PendingLaunch] = {}
        self._sessions: dict[str, BrowserSession] = {}

    def create_launch(self, working_directory: str) -> str:
        try:
            workspace = Path(working_directory).expanduser().resolve(strict=True)
        except OSError as error:
            raise WebUiError(
                422,
                "invalid_working_directory",
                "working directory does not exist",
            ) from error
        if not workspace.is_dir():
            raise WebUiError(
                422, "invalid_working_directory", "working directory is not a directory"
            )
        self._prune_launches()
        while len(self._launches) >= MAX_PENDING_LAUNCHES:
            self._launches.pop(next(iter(self._launches)))
        token = secrets.token_urlsafe(32)
        self._launches[token] = PendingLaunch(
            working_directory=str(workspace),
            expires_at=time.monotonic() + LAUNCH_LIFETIME_SECONDS,
        )
        return f"{self.origin}/_hames/v1/launch/{token}"

    def exchange_launch(self, token: str) -> tuple[str, BrowserSession]:
        self._prune_launches()
        launch = self._launches.pop(token, None)
        if launch is None:
            raise WebUiError(401, "invalid_launch_token", "launch token is invalid or expired")
        session_token = secrets.token_urlsafe(32)
        session = BrowserSession(
            working_directory=launch.working_directory,
            csrf_token=secrets.token_urlsafe(32),
        )
        while len(self._sessions) >= MAX_BROWSER_SESSIONS:
            self._sessions.pop(next(iter(self._sessions)))
        self._sessions[session_token] = session
        return session_token, session

    def authorize(self, request: Request) -> BrowserSession:
        token = request.cookies.get(WEB_SESSION_COOKIE, "")
        session = next(
            (
                value
                for candidate, value in self._sessions.items()
                if hmac.compare_digest(candidate, token)
            ),
            None,
        )
        if session is None:
            raise WebUiError(
                401, "web_session_required", "an authenticated web session is required"
            )
        self.require_host(request)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin", "")
            csrf = request.headers.get(WEB_CSRF_HEADER, "")
            if not hmac.compare_digest(origin, self.origin) or not hmac.compare_digest(
                csrf, session.csrf_token
            ):
                raise WebUiError(
                    403, "csrf_check_failed", "web mutation origin or token is invalid"
                )
        return session

    def require_host(self, request: Request) -> None:
        if not hmac.compare_digest(request.headers.get("host", ""), self.authority):
            raise WebUiError(421, "invalid_host", "web request host does not match the gateway")

    def _prune_launches(self) -> None:
        now = time.monotonic()
        self._launches = {
            token: launch for token, launch in self._launches.items() if launch.expires_at > now
        }


def install_web_routes(
    app: FastAPI,
    web: WebUi,
    *,
    bearer_dependencies: list[DependsParameter],
) -> None:
    @app.post(
        "/v1/web/launch",
        dependencies=bearer_dependencies,
        response_model=WebLaunchResponse,
    )
    async def create_web_launch(request: WebLaunchRequest) -> WebLaunchResponse:
        return WebLaunchResponse(url=web.create_launch(request.working_directory))

    @app.get("/_hames/v1/launch/{token}")
    async def exchange_web_launch(request: Request, token: str) -> Response:
        web.require_host(request)
        session_token, _ = web.exchange_launch(token)
        response = RedirectResponse("/chat", status_code=303)
        response.set_cookie(
            WEB_SESSION_COOKIE,
            session_token,
            httponly=True,
            samesite="strict",
            path="/",
        )
        return _secure(response)

    @app.get("/_hames/v1/bootstrap", response_model=WebBootstrap)
    async def web_bootstrap(request: Request) -> Response:
        session = web.authorize(request)
        return _secure(
            JSONResponse(
                WebBootstrap(
                    protocol_version=WEB_PROTOCOL_VERSION,
                    gateway_protocol_version=PROTOCOL_VERSION,
                    working_directory=session.working_directory,
                    csrf_token=session.csrf_token,
                ).model_dump()
            )
        )

    @app.get("/{path:path}")
    async def web_asset(request: Request, path: str) -> Response:
        web.require_host(request)
        if path in {"v1", "_hames"} or path.startswith(("v1/", "_hames/")):
            return _secure(_error_response(404, "not_found", "route not found"))
        requested = path or "index.html"
        candidate = (web.asset_root / requested).resolve()
        if candidate.is_relative_to(web.asset_root) and candidate.is_file():
            return _secure(_file_response(candidate, immutable=requested != "index.html"))
        if "." in Path(requested).name:
            return _secure(_error_response(404, "asset_not_found", "web asset not found"))
        index = web.asset_root / "index.html"
        if not index.is_file():
            return _secure(_error_response(503, "web_assets_missing", "web assets are unavailable"))
        return _secure(_file_response(index, immutable=False))


def web_error_response(error: WebUiError) -> Response:
    return _secure(_error_response(error.status_code, error.code, str(error)))


def _file_response(path: Path, *, immutable: bool) -> FileResponse:
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": ("public, max-age=31536000, immutable" if immutable else "no-store")
        },
    )


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": False,
                "details": {},
            }
        },
    )


def _secure(response: Response) -> Response:
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    return response
