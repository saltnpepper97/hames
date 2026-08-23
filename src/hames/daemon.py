"""Persistent local gateway lifecycle."""

from __future__ import annotations

import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import cast

import httpx
import uvicorn

from hames import PROTOCOL_VERSION, __version__
from hames.config import HamesConfig, load_config
from hames.gateway import GatewayState, create_app
from hames.logging import configure_logging
from hames.paths import HamesPaths
from hames.providers.base import JSON_OBJECT


class GatewayAlreadyRunning(RuntimeError):
    pass


class PidLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: object | None = None

    def __enter__(self) -> PidLock:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.close()
            raise GatewayAlreadyRunning("gateway PID lock is already held") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        os.fchmod(handle.fileno(), 0o600)
        self._handle = handle
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        handle = self._handle
        if handle is not None and hasattr(handle, "close"):
            self.path.unlink(missing_ok=True)
            handle.close()  # type: ignore[union-attr]


@dataclass(frozen=True, slots=True)
class GatewayProcessStatus:
    running: bool
    pid: int | None
    healthy: bool
    url: str
    protocol_version: int | None = None
    version: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "running": self.running,
                "pid": self.pid,
                "healthy": self.healthy,
                "url": self.url,
                "protocol_version": self.protocol_version,
                "version": self.version,
            },
            sort_keys=True,
        )


def gateway_url(config: HamesConfig) -> str:
    host = "127.0.0.1" if config.gateway.host == "localhost" else config.gateway.host
    return f"http://{host}:{config.gateway.port}"


def read_pid(paths: HamesPaths) -> int | None:
    try:
        value = int(paths.gateway_pid.read_text(encoding="utf-8").strip())
        os.kill(value, 0)
        return value
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def gateway_status(paths: HamesPaths) -> GatewayProcessStatus:
    config = load_config(paths)
    url = gateway_url(config)
    pid = read_pid(paths)
    try:
        response = httpx.get(f"{url}/v1/health", timeout=0.75)
        response.raise_for_status()
        data = JSON_OBJECT.validate_python(cast(object, response.json()))
        protocol = data.get("protocol_version")
        version = data.get("version")
        return GatewayProcessStatus(
            running=True,
            pid=pid,
            healthy=True,
            url=url,
            protocol_version=int(protocol) if isinstance(protocol, int) else None,
            version=str(version) if version is not None else None,
        )
    except (httpx.HTTPError, ValueError):
        return GatewayProcessStatus(running=pid is not None, pid=pid, healthy=False, url=url)


def serve(paths: HamesPaths) -> None:
    paths.ensure_foundation()
    config = load_config(paths)
    configure_logging(paths.logs / "gateway.log", level=config.logging.level)
    with PidLock(paths.gateway_pid):
        state = GatewayState.create(paths)
        uvicorn.run(
            create_app(state),
            host=config.gateway.host,
            port=config.gateway.port,
            log_config=None,
            access_log=False,
        )


def start(paths: HamesPaths, *, wait_seconds: float = 10.0) -> GatewayProcessStatus:
    paths.ensure_foundation()
    current = gateway_status(paths)
    if current.healthy:
        if current.protocol_version == PROTOCOL_VERSION and current.version == __version__:
            return current
        if current.pid is None:
            raise RuntimeError("incompatible process is using the configured gateway port")
        stop(paths, wait_seconds=wait_seconds)
    log_handle = (paths.logs / "gateway-bootstrap.log").open("ab")
    try:
        subprocess.Popen(
            [sys.executable, "-m", "hames.cli", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        log_handle.close()
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        status = gateway_status(paths)
        if status.healthy:
            if status.protocol_version != PROTOCOL_VERSION:
                raise RuntimeError("gateway protocol version does not match this installation")
            return status
        time.sleep(0.05)
    raise RuntimeError(
        f"gateway did not become healthy; see {paths.logs / 'gateway-bootstrap.log'}"
    )


def stop(paths: HamesPaths, *, wait_seconds: float = 10.0) -> GatewayProcessStatus:
    status = gateway_status(paths)
    if status.pid is None:
        paths.gateway_pid.unlink(missing_ok=True)
        return status
    os.kill(status.pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if read_pid(paths) is None:
            break
        time.sleep(0.05)
    return gateway_status(paths)
