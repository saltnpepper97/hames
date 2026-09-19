"""Grok Build subscription provider using the local `grok login` session."""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode

import httpx

from hames.providers.base import JSON_OBJECT, ProviderError
from hames.providers.xai import XaiProvider

GROK_PROXY_URL = "https://cli-chat-proxy.grok.com/v1"
GROK_TOKEN_URL = "https://auth.x.ai/oauth2/token"
DEFAULT_CLIENT_VERSION = "1.0.34"
_VERSION = re.compile(r"\b(\d+\.\d+\.\d+)\b")


class GrokProvider(XaiProvider):
    adapter = "grok"
    brand = "Grok Build"

    def __init__(
        self,
        base_url: str = GROK_PROXY_URL,
        *,
        profile_id: str = "grok",
        timeout_seconds: float = 600.0,
        default_model: str = "",
        supported_reasoning_efforts: list[str] | None = None,
        environ: Mapping[str, str] | None = None,
        client: httpx.AsyncClient | None = None,
        grok_home: Path | None = None,
        client_version: str | None = None,
    ) -> None:
        super().__init__(
            base_url,
            profile_id=profile_id,
            api_key_env="",
            timeout_seconds=timeout_seconds,
            default_model=default_model,
            supported_reasoning_efforts=supported_reasoning_efforts,
            environ=environ,
            client=client,
        )
        self.grok_home = Path(grok_home) if grok_home is not None else Path.home() / ".grok"
        self._client_version = client_version
        self._account_usage: dict[str, Any] | None = None

    def cached_account_rate_limits(self) -> dict[str, Any] | None:
        return self._account_usage

    async def account_rate_limits(self) -> dict[str, Any]:
        headers = await asyncio.to_thread(self._billing_headers)
        response = await self.client.get(
            f"{self.base_url.rstrip('/')}/billing",
            params={"format": "credits"},
            headers=headers,
            timeout=4.0,
        )
        if not response.is_success:
            raise ProviderError("usage_unavailable", "Grok account usage is unavailable")
        usage = _normalize_account_usage(response.json())
        self._account_usage = usage
        return usage

    def _billing_headers(self) -> dict[str, str]:
        headers = self._headers()
        _, session = _selected_session(json.loads((self.grok_home / "auth.json").read_text()))
        if session.get("user_id"):
            headers["x-userid"] = str(session["user_id"])
        return headers

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._session_token()}",
            "X-XAI-Token-Auth": "xai-grok-cli",
            "x-grok-client-version": self._version(),
        }

    def _version(self) -> str:
        if self._client_version:
            return self._client_version
        grok = shutil.which("grok")
        if grok:
            try:
                completed = subprocess.run(
                    [grok, "version"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                match = _VERSION.search(completed.stdout or completed.stderr)
                if match:
                    self._client_version = match.group(1)
                    return self._client_version
            except (OSError, subprocess.SubprocessError, TimeoutError):
                pass
        self._client_version = DEFAULT_CLIENT_VERSION
        return self._client_version

    def _session_token(self) -> str:
        path = self.grok_home / "auth.json"
        try:
            raw = json.loads(path.read_text())
        except FileNotFoundError as exc:
            raise ProviderError(
                "provider_not_configured",
                "Grok Build is not signed in; run `grok login` or `hames setup grok`",
            ) from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ProviderError(
                "provider_not_configured",
                "Grok Build login could not be read from ~/.grok/auth.json",
            ) from exc
        if not isinstance(raw, dict) or not raw:
            raise ProviderError(
                "provider_not_configured",
                "Grok Build is not signed in; run `grok login` or `hames setup grok`",
            )
        sessions = cast(dict[str, Any], raw)
        issuer, session = _selected_session(sessions)
        token = str(session.get("key") or "").strip()
        if not token:
            raise ProviderError(
                "provider_not_configured",
                "Grok Build is not signed in; run `grok login` or `hames setup grok`",
            )
        if _session_fresh(session):
            return token
        refreshed = _refresh_session(session)
        sessions[issuer] = {**session, **refreshed}
        _write_auth(path, sessions)
        return str(refreshed["key"])


def _normalize_account_usage(body: Any) -> dict[str, Any]:
    config = cast(dict[str, Any], body).get("config") if isinstance(body, dict) else None
    if not isinstance(config, dict):
        raise ProviderError("usage_unavailable", "Grok did not provide account limits")
    config = cast(dict[str, Any], config)
    used = config.get("creditUsagePercent")
    if used is None:
        limit = config.get("monthlyLimit")
        consumed = config.get("used")
        if isinstance(limit, dict) and isinstance(consumed, dict):
            budget = cast(dict[str, Any], limit).get("val", 0)
            amount = cast(dict[str, Any], consumed).get("val", 0)
            try:
                used = float(amount) / float(budget) * 100 if float(budget) > 0 else None
            except (TypeError, ValueError):
                pass
    if (
        isinstance(used, bool)
        or not isinstance(used, int | float)
        or not math.isfinite(used)
        or used < 0
    ):
        raise ProviderError("usage_unavailable", "Grok did not provide account limits")
    period = config.get("currentPeriod")
    period = cast(dict[str, Any], period) if isinstance(period, dict) else {}
    kind = str(period.get("type", ""))
    label = (
        "Weekly limit"
        if "WEEKLY" in kind
        else "Monthly limit"
        if "MONTHLY" in kind
        else "Usage limit"
    )
    reset = period.get("end") or config.get("billingPeriodEnd")
    return {
        "label": label,
        "observed_at": time.time(),
        "window": {
            "used": min(100.0, used),
            "remaining": max(0.0, 100.0 - used),
            "reset_at": reset if isinstance(reset, str) else None,
            "window_minutes": None,
        },
    }


def _selected_session(sessions: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    chosen: tuple[str, dict[str, Any]] | None = None
    latest = datetime.min.replace(tzinfo=UTC)
    for issuer, value in sessions.items():
        if not isinstance(value, dict):
            continue
        session = cast(dict[str, Any], value)
        if not str(session.get("key") or "").strip():
            continue
        expiry = _parse_expiry(session.get("expires_at")) or latest
        if chosen is None or expiry >= latest:
            chosen = (issuer, session)
            latest = expiry
    if chosen is None:
        raise ProviderError(
            "provider_not_configured",
            "Grok Build is not signed in; run `grok login` or `hames setup grok`",
        )
    return chosen


def _session_fresh(session: Mapping[str, Any]) -> bool:
    expiry = _parse_expiry(session.get("expires_at"))
    if expiry is None:
        return True
    return expiry - timedelta(seconds=60) > datetime.now(UTC)


def _parse_expiry(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _refresh_session(session: Mapping[str, Any]) -> dict[str, Any]:
    refresh_token = str(session.get("refresh_token") or "").strip()
    client_id = str(session.get("oidc_client_id") or "").strip()
    if not refresh_token or not client_id:
        raise ProviderError(
            "provider_not_configured",
            "Grok Build login expired; run `grok login`",
        )
    try:
        response = httpx.post(
            GROK_TOKEN_URL,
            content=urlencode(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": refresh_token,
                    "client_id": client_id,
                }
            ),
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15.0,
        )
        response.raise_for_status()
        body = JSON_OBJECT.validate_python(cast(object, response.json()))
    except (httpx.HTTPError, ValueError) as exc:
        raise ProviderError(
            "provider_not_configured",
            "Grok Build login expired; run `grok login`",
        ) from exc
    access = str(body.get("access_token") or "").strip()
    if not access:
        raise ProviderError(
            "provider_not_configured",
            "Grok Build login expired; run `grok login`",
        )
    updated = {"key": access}
    next_refresh = str(body.get("refresh_token") or "").strip()
    if next_refresh:
        updated["refresh_token"] = next_refresh
    expires_in = body.get("expires_in")
    if isinstance(expires_in, int | float) and expires_in > 0:
        updated["expires_at"] = (datetime.now(UTC) + timedelta(seconds=float(expires_in))).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    return updated


def _write_auth(path: Path, sessions: dict[str, Any]) -> None:
    payload = json.dumps(sessions, indent=2) + "\n"
    handle, temporary_path = tempfile.mkstemp(prefix=".auth-", dir=path.parent)
    try:
        with os.fdopen(handle, "w") as file:
            file.write(payload)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary_path, path)
        path.chmod(0o600)
    finally:
        if os.path.exists(temporary_path):
            os.unlink(temporary_path)
