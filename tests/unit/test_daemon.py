from __future__ import annotations

import logging
import socket
from pathlib import Path

import httpx
import pytest

from hames.daemon import (
    PidLock,
    hames_listener_pid,
    read_pid,
    start,
    stop,
)
from hames.logging import configure_logging
from hames.paths import HamesPaths


def test_pid_lock_is_single_instance(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    with PidLock(hames_paths.gateway_pid):
        assert read_pid(hames_paths) is not None
        with pytest.raises(RuntimeError, match="already held"):
            with PidLock(hames_paths.gateway_pid):
                pass
    assert not hames_paths.gateway_pid.exists()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _home_on_port(root: Path, port: int) -> HamesPaths:
    paths = HamesPaths.resolve(root=root)
    paths.ensure_foundation()
    paths.config_file.write_text(
        f'[gateway]\nhost = "127.0.0.1"\nport = {port}\n',
        encoding="utf-8",
    )
    return paths


def test_hames_listener_pid_ignores_non_hames_sockets() -> None:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        assert hames_listener_pid(port) is None


def test_start_replaces_gateway_that_rejects_this_homes_token(tmp_path: Path) -> None:
    port = _free_port()
    first = _home_on_port(tmp_path / "first", port)
    second = _home_on_port(tmp_path / "second", port)
    started = start(first)
    try:
        assert started.healthy
        first_token = first.read_gateway_token()
        rejected = httpx.get(
            f"{started.url}/v1/providers",
            headers={"Authorization": f"Bearer {second.read_gateway_token()}"},
            timeout=2,
        )
        assert rejected.status_code == 401
        replaced = start(second)
        assert replaced.healthy
        accepted = httpx.get(
            f"{replaced.url}/v1/providers",
            headers={"Authorization": f"Bearer {second.read_gateway_token()}"},
            timeout=2,
        )
        assert accepted.status_code == 200
        stale = httpx.get(
            f"{replaced.url}/v1/providers",
            headers={"Authorization": f"Bearer {first_token}"},
            timeout=2,
        )
        assert stale.status_code == 401
    finally:
        stop(second)
        stop(first)


def test_logging_redacts_sensitive_messages(tmp_path: Path) -> None:
    log_file = tmp_path / "hames.log"
    configure_logging(log_file)
    logging.getLogger("test").warning("Authorization: Bearer extremely-secret")
    content = log_file.read_text(encoding="utf-8")
    assert "extremely-secret" not in content
    assert "[redacted]" in content


def test_process_identity_requires_exact_invocation_and_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    from unittest.mock import Mock

    import psutil

    from hames.daemon import is_owned_gateway_process

    process = Mock()
    process.uids.return_value.effective = os.geteuid()

    def get_process(_pid: int) -> Mock:
        return process

    monkeypatch.setattr("hames.daemon.psutil.Process", get_process)
    process.cmdline.return_value = ["/venv with spaces/bin/python", "-m", "hames.cli", "serve"]
    assert is_owned_gateway_process(123)
    process.cmdline.return_value = ["other", "hames.cli", "serve"]
    assert not is_owned_gateway_process(123)
    process.cmdline.return_value = ["python", "-c", "hames.cli", "serve"]
    assert not is_owned_gateway_process(123)
    process.cmdline.return_value = ["python", "-m", "hames.cli", "serve"]
    process.uids.return_value.effective = os.geteuid() + 1
    assert not is_owned_gateway_process(123)
    process.uids.side_effect = psutil.AccessDenied(123)
    assert not is_owned_gateway_process(123)
    assert not is_owned_gateway_process(0)
    assert not is_owned_gateway_process(-1)


def test_listener_discovery_skips_denied_and_non_loopback_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os
    from unittest.mock import Mock

    import psutil

    from hames.daemon import loopback_listener_pid

    denied = Mock()
    denied.uids.side_effect = psutil.AccessDenied(1)
    public = Mock(pid=2)
    public.uids.return_value.effective = os.geteuid()
    public.net_connections.return_value = [
        Mock(status=psutil.CONN_LISTEN, laddr=Mock(ip="0.0.0.0", port=7410))
    ]
    loopback = Mock(pid=3)
    loopback.uids.return_value.effective = os.geteuid()
    loopback.net_connections.return_value = [
        Mock(status=psutil.CONN_LISTEN, laddr=Mock(ip="::1", port=7410))
    ]
    monkeypatch.setattr(
        "hames.daemon.psutil.process_iter", lambda: iter([denied, public, loopback])
    )
    assert loopback_listener_pid(7410) == 3
    assert loopback_listener_pid(7411) is None


def test_gateway_can_be_recovered_without_pid_file(tmp_path: Path) -> None:
    from hames.daemon import hames_listener_pid

    port = _free_port()
    first = _home_on_port(tmp_path / "first", port)
    second = _home_on_port(tmp_path / "second", port)
    started = start(first)
    try:
        assert started.pid is not None
        assert hames_listener_pid(port) == started.pid
        first.gateway_pid.unlink()
        replaced = start(second)
        assert replaced.healthy
        assert replaced.pid != started.pid
    finally:
        stop(second)
        stop(first)
