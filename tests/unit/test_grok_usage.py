import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from hames.gateway import _attach_grok_account_usage
from hames.inspection import UsageProjection
from hames.providers.base import ProviderError
from hames.providers.grok import GrokProvider, _normalize_account_usage

# pyright: reportPrivateUsage=false


@pytest.mark.asyncio
async def test_grok_account_usage_reads_billing_without_generation(tmp_path: Path):
    (tmp_path / "auth.json").write_text(
        json.dumps({"issuer": {"key": "test-token", "user_id": "test-user"}})
    )

    def handle(request: httpx.Request):
        assert request.url.path == "/v1/billing"
        assert request.url.params["format"] == "credits"
        assert request.headers["authorization"] == "Bearer test-token"
        assert request.headers["x-userid"] == "test-user"
        return httpx.Response(
            200,
            json={
                "config": {
                    "creditUsagePercent": 88.5,
                    "currentPeriod": {
                        "type": "USAGE_PERIOD_TYPE_WEEKLY",
                        "end": "2026-09-20T21:00:00Z",
                    },
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = GrokProvider(client=client, grok_home=tmp_path, client_version="1.0.34")
        usage = await provider.account_rate_limits()
        assert usage["label"] == "Weekly limit"
        assert usage["window"]["used"] == 88.5
        assert usage["window"]["reset_at"] == "2026-09-20T21:00:00Z"
        assert provider.cached_account_rate_limits() == usage


@pytest.mark.parametrize(
    "config",
    [
        None,
        {},
        {"creditUsagePercent": True},
        {"creditUsagePercent": float("nan")},
        {"creditUsagePercent": -1},
    ],
)
def test_missing_or_invalid_grok_limits_are_not_zero(config: Any) -> None:
    with pytest.raises(ProviderError):
        _normalize_account_usage({"config": config})


def test_legacy_and_zero_grok_usage():
    assert _normalize_account_usage({"config": {"creditUsagePercent": 0}})["window"]["used"] == 0
    assert (
        _normalize_account_usage(
            {"config": {"monthlyLimit": {"val": "200"}, "used": {"val": "50"}}}
        )["window"]["used"]
        == 25
    )


@pytest.mark.asyncio
async def test_optional_grok_failure_keeps_local_totals():
    class Unavailable(GrokProvider):
        async def account_rate_limits(self):
            raise RuntimeError("private upstream details")

    usage = UsageProjection(estimated_input_tokens=123)
    await _attach_grok_account_usage(usage, Unavailable())
    assert usage.estimated_input_tokens == 123
    assert usage.grok_account_configured
    assert usage.grok_account_usage is None
    assert "private" not in usage.grok_account_usage_error


@pytest.mark.asyncio
async def test_billing_denial_does_not_expose_response(tmp_path: Path):
    (tmp_path / "auth.json").write_text(json.dumps({"issuer": {"key": "test-token"}}))
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(401, text="private billing details"))
    ) as client:
        provider = GrokProvider(client=client, grok_home=tmp_path, client_version="1.0.34")
        with pytest.raises(ProviderError, match="Grok account usage is unavailable"):
            await provider.account_rate_limits()
        assert provider.cached_account_rate_limits() is None
