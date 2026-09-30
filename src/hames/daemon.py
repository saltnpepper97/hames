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
import psutil
import uvicorn

from hames import PROTOCOL_VERSION, __version__
from hames.config import HamesConfig, load_config
from hames.gateway import GatewayState, create_app
from hames.logging import configure_logging
from hames.paths import HamesPaths
from hames.providers.base import JSON_OBJECT
from hames.search_service import SearchService


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
    search: dict[str, object] | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "running": self.running,
                "pid": self.pid,
                "healthy": self.healthy,
                "url": self.url,
                "protocol_version": self.protocol_version,
                "version": self.version,
                "search": self.search,
            },
            sort_keys=True,
        )


def gateway_url(config: HamesConfig) -> str:
    host = "127.0.0.1" if config.gateway.host == "localhost" else config.gateway.host
    return f"http://{host}:{config.gateway.port}"


def read_pid(paths: HamesPaths) -> int | None:
    try:
        value = int(paths.gateway_pid.read_text(encoding="utf-8").strip())
        if value <= 0:
            return None
        os.kill(value, 0)
        return value
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return None


def is_owned_gateway_process(pid: int) -> bool:
    """Identify our user's module-launched gateway without platform-specific /proc access."""
    if pid <= 0:
        return False
    try:
        process = psutil.Process(pid)
        if process.uids().effective != os.geteuid():
            return False
        command = process.cmdline()
    except (psutil.Error, OSError):
        return False
    # Match the invocation, not arbitrary arguments containing these words.
    return len(command) >= 4 and command[1:4] == ["-m", "hames.cli", "serve"]


def loopback_listener_pid(port: int) -> int | None:
    """Find a same-user loopback listener on Linux or macOS without elevated access."""
    for process in psutil.process_iter():
        try:
            if process.uids().effective != os.geteuid():
                continue
            # System-wide net_connections requires root on macOS. Per-process
            # inspection works for accessible same-user processes.
            for connection in process.net_connections(kind="tcp"):
                if (
                    connection.status == psutil.CONN_LISTEN
                    and connection.laddr
                    and connection.laddr.port == port
                    and connection.laddr.ip in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
                ):
                    return process.pid
        except (psutil.Error, OSError):
            # Processes may exit during inspection or deny access. Never guess.
            continue
    return None


def hames_listener_pid(port: int) -> int | None:
    pid = loopback_listener_pid(port)
    if pid is not None and is_owned_gateway_process(pid):
        return pid
    return None


def _token_accepted(paths: HamesPaths, url: str) -> bool:
    try:
        token = paths.read_gateway_token()
    except OSError:
        return False
    if not token:
        return False
    try:
        response = httpx.get(
            f"{url}/v1/providers",
            headers={"Authorization": f"Bearer {token}"},
            timeout=0.75,
        )
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def _compatible_gateway(status: GatewayProcessStatus) -> bool:
    return status.protocol_version == PROTOCOL_VERSION and status.version == __version__


def _terminate_pid(pid: int, wait_seconds: float) -> None:
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.05)


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
        search = data.get("search")
        return GatewayProcessStatus(
            running=True,
            pid=pid,
            healthy=True,
            url=url,
            protocol_version=int(protocol) if isinstance(protocol, int) else None,
            version=str(version) if version is not None else None,
            search=cast(dict[str, object], search) if isinstance(search, dict) else None,
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
    config = load_config(paths)
    current = gateway_status(paths)
    if current.healthy and _compatible_gateway(current) and _token_accepted(paths, current.url):
        return current
    if current.healthy:
        occupier = current.pid
        if occupier is None:
            occupier = hames_listener_pid(config.gateway.port)
        if occupier is None or not is_owned_gateway_process(occupier):
            raise RuntimeError("incompatible process is using the configured gateway port")
        _terminate_pid(occupier, wait_seconds)
        paths.gateway_pid.unlink(missing_ok=True)
    log_handle = (paths.logs / "gateway-bootstrap.log").open("ab")
    child_env = os.environ.copy()
    child_env["HAMES_HOME"] = str(paths.root)
    try:
        subprocess.Popen(
            [sys.executable, "-m", "hames.cli", "serve"],
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            close_fds=True,
            env=child_env,
        )
    finally:
        log_handle.close()
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        status = gateway_status(paths)
        if status.healthy:
            if status.protocol_version != PROTOCOL_VERSION:
                raise RuntimeError("gateway protocol version does not match this installation")
            if _token_accepted(paths, status.url):
                return status
        time.sleep(0.05)
    raise RuntimeError(
        f"gateway did not become healthy; see {paths.logs / 'gateway-bootstrap.log'}"
    )


def stop(paths: HamesPaths, *, wait_seconds: float = 10.0) -> GatewayProcessStatus:
    status = gateway_status(paths)
    if status.pid is None:
        paths.gateway_pid.unlink(missing_ok=True)
        SearchService(paths).stop()
        return status
    if not is_owned_gateway_process(status.pid):
        raise RuntimeError("gateway PID file does not identify an owned Hames gateway process")
    _terminate_pid(status.pid, wait_seconds)
    paths.gateway_pid.unlink(missing_ok=True)
    SearchService(paths).stop()
    return gateway_status(paths)
