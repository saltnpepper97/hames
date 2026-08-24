"""Private, Hames-managed SearXNG container lifecycle."""

from __future__ import annotations

import hashlib
import os
import secrets
import shutil
import socket
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from typing import Literal, cast

import httpx
import yaml
from pydantic import BaseModel, ConfigDict, Field

from hames.paths import HamesPaths

SEARXNG_RELEASE = "2026.8.22-9fea41204"
SEARXNG_IMAGE = (
    "docker.io/searxng/searxng@"
    "sha256:11a9b34cdc0b1ec2b991470a2762ecb5a1a531898289fb51dcd015260450729e"
)
SEARCH_PORT_START = 7412
SEARCH_PORT_END = 7499
SERVICE_LABEL = "io.hames.service"
HOME_LABEL = "io.hames.home"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchSetupState(_StrictModel):
    schema_version: int = 1
    configured: bool = True
    enabled: bool = False
    runtime: Literal["podman", "docker", ""] = ""
    port: int = Field(default=SEARCH_PORT_START, ge=1, le=65535)
    image: str = SEARXNG_IMAGE
    container_name: str


class SearchStatus(_StrictModel):
    status: Literal["unconfigured", "disabled", "starting", "ready", "degraded"]
    configured: bool
    enabled: bool
    runtime: str = ""
    image: str = SEARXNG_IMAGE
    release: str = SEARXNG_RELEASE
    container_name: str = ""
    url: str = ""
    error: str = ""


class SearchService:
    """Own exactly one loopback-only SearXNG container for a Hames home."""

    def __init__(self, paths: HamesPaths) -> None:
        self.paths = paths

    def setup(self, *, enabled: bool) -> SearchStatus:
        self.paths.ensure_foundation()
        existing = self.load_state()
        container_name = existing.container_name if existing is not None else self._container_name()
        gateway_port = self._gateway_port()
        port = (
            existing.port
            if existing is not None and existing.port != gateway_port
            else self._select_port(exclude={gateway_port})
        )
        runtime = self._detect_runtime() if enabled else (existing.runtime if existing else "")
        state = SearchSetupState(
            enabled=enabled,
            runtime=runtime,
            port=port,
            image=SEARXNG_IMAGE,
            container_name=container_name,
        )
        self._write_state(state)
        if (
            existing is not None
            and existing.runtime
            and existing.port != state.port
            and self._container_exists(existing)
        ):
            self._remove_container(existing)
        if not enabled:
            if existing is not None and existing.runtime:
                self._stop_container(existing, ignore_errors=True)
            return self.status()
        return self.ensure_running()

    def load_state(self) -> SearchSetupState | None:
        try:
            return SearchSetupState.model_validate_json(
                self.paths.search_state.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None

    def status(self, *, probe: bool = True) -> SearchStatus:
        state = self.load_state()
        if state is None:
            return SearchStatus(status="unconfigured", configured=False, enabled=False)
        if not state.enabled:
            return self._status_for(state, "disabled")
        if not state.runtime:
            return self._status_for(
                state, "degraded", "Docker or Podman is not available to the current user"
            )
        try:
            if not self._container_exists(state):
                return self._status_for(state, "degraded", "managed SearXNG container is absent")
            if not self._container_running(state):
                return self._status_for(state, "degraded", "managed SearXNG container is stopped")
            if probe and not self._healthy(state):
                return self._status_for(state, "degraded", "SearXNG health check failed")
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return self._status_for(state, "degraded", str(exc))
        return self._status_for(state, "ready")

    def _status_for(
        self,
        state: SearchSetupState,
        status: Literal["disabled", "ready", "degraded"],
        error: str = "",
    ) -> SearchStatus:
        return SearchStatus(
            status=status,
            configured=True,
            enabled=state.enabled,
            runtime=state.runtime,
            image=state.image,
            container_name=state.container_name,
            url=self.url(state) if state.enabled else "",
            error=error,
        )

    def ensure_running(self) -> SearchStatus:
        state = self.load_state()
        if state is None or not state.enabled:
            return self.status()
        runtime = state.runtime or self._detect_runtime()
        if not runtime:
            return self.status()
        if runtime != state.runtime:
            state = state.model_copy(update={"runtime": runtime})
            self._write_state(state)
        try:
            self._ensure_directories()
            self._ensure_settings()
            if self._container_exists(state):
                if self._container_image(state) != state.image:
                    self._remove_container(state)
                    self._create_container(state)
                elif not self._container_running(state):
                    self._run([state.runtime, "start", state.container_name], timeout=30)
            else:
                self._run([state.runtime, "pull", state.image], timeout=300)
                self._create_container(state)
            if not self._wait_healthy(state):
                raise RuntimeError("SearXNG did not become healthy")
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            value = self.status(probe=False)
            return value.model_copy(update={"status": "degraded", "error": str(exc)})
        return self.status()

    def stop(self) -> SearchStatus:
        state = self.load_state()
        if state is not None and state.runtime:
            self._stop_container(state, ignore_errors=False)
        return self.status(probe=False)

    def restart(self) -> SearchStatus:
        state = self.load_state()
        if state is None or not state.enabled:
            return self.status()
        if state.runtime and self._container_exists(state):
            self._run([state.runtime, "restart", state.container_name], timeout=45)
            if not self._wait_healthy(state):
                return self.status(probe=False).model_copy(
                    update={"status": "degraded", "error": "SearXNG restart health check failed"}
                )
            return self.status()
        return self.ensure_running()

    def remove_managed_container(self, *, remove_cache: bool = False) -> None:
        """Remove only the ownership-verified container, retaining user configuration."""

        state = self.load_state()
        if state is not None and state.runtime and self._container_exists(state):
            self._remove_container(state)
        if state is not None and state.runtime and remove_cache:
            name = self._cache_volume_name()
            service = self._run(
                [
                    state.runtime,
                    "volume",
                    "inspect",
                    "--format",
                    f'{{{{ index .Labels "{SERVICE_LABEL}" }}}}',
                    name,
                ],
                timeout=10,
                check=False,
            )
            home = self._run(
                [
                    state.runtime,
                    "volume",
                    "inspect",
                    "--format",
                    f'{{{{ index .Labels "{HOME_LABEL}" }}}}',
                    name,
                ],
                timeout=10,
                check=False,
            )
            if (
                service.stdout.strip() == "search-cache"
                and home.stdout.strip() == self._home_label()
            ):
                self._run([state.runtime, "volume", "rm", name], timeout=20)

    def update(self) -> SearchStatus:
        state = self.load_state()
        if state is None or not state.enabled:
            return self.status()
        runtime = state.runtime or self._detect_runtime()
        if not runtime:
            return self._status_for(
                state, "degraded", "Docker or Podman is not available to the current user"
            )
        state = state.model_copy(update={"runtime": runtime, "image": SEARXNG_IMAGE})
        self._write_state(state)
        old_image = self._container_image(state) if self._container_exists(state) else ""
        removed = False
        try:
            self._run([state.runtime, "pull", state.image], timeout=300)
            if old_image == state.image:
                return self.ensure_running()
            if self._container_exists(state):
                self._remove_container(state)
                removed = True
            self._create_container(state)
            if not self._wait_healthy(state):
                raise RuntimeError("updated SearXNG container failed its health check")
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            if old_image and old_image != state.image:
                if not removed:
                    state = state.model_copy(update={"image": old_image})
                    self._write_state(state)
                else:
                    try:
                        if self._container_exists(state):
                            self._remove_container(state)
                        self._create_container(state, image=old_image)
                        if self._wait_healthy(state):
                            state = state.model_copy(update={"image": old_image})
                            self._write_state(state)
                    except (OSError, RuntimeError, subprocess.SubprocessError):
                        pass
            return self.status(probe=False).model_copy(
                update={"status": "degraded", "error": str(exc)}
            )
        return self.status()

    @staticmethod
    def url(state: SearchSetupState) -> str:
        return f"http://127.0.0.1:{state.port}"

    def _write_state(self, state: SearchSetupState) -> None:
        self.paths.search_service.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.paths.search_service.chmod(0o700)
        _atomic_private_write(self.paths.search_state, state.model_dump_json(indent=2) + "\n")

    def _ensure_directories(self) -> None:
        self.paths.services.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.paths.services.chmod(0o700)
        self.paths.search_service.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.paths.search_service.chmod(0o700)
        self.paths.search_config.mkdir(mode=0o755, parents=True, exist_ok=True)
        self.paths.search_config.chmod(0o755)

    def _ensure_settings(self) -> None:
        path = self.paths.search_config / "settings.yml"
        if path.exists():
            return
        settings = {
            "use_default_settings": True,
            "general": {"instance_name": "Hames Search", "debug": False},
            "search": {"safe_search": 1, "formats": ["html", "json"]},
            "server": {
                "secret_key": secrets.token_hex(32),
                "limiter": False,
                "image_proxy": False,
            },
        }
        _atomic_private_write(path, yaml.safe_dump(settings, sort_keys=False), mode=0o644)

    def _container_name(self) -> str:
        digest = hashlib.sha256(os.fspath(self.paths.root).encode()).hexdigest()[:12]
        return f"hames-searxng-{digest}"

    def _home_label(self) -> str:
        return hashlib.sha256(os.fspath(self.paths.root).encode()).hexdigest()

    def _cache_volume_name(self) -> str:
        return self._container_name().replace("searxng", "searxng-cache")

    def _detect_runtime(self) -> Literal["podman", "docker", ""]:
        for runtime in ("podman", "docker"):
            if shutil.which(runtime) is None:
                continue
            try:
                self._run([runtime, "info"], timeout=10)
            except (OSError, RuntimeError, subprocess.SubprocessError):
                continue
            return runtime
        return ""

    def _select_port(self, *, exclude: set[int] | None = None) -> int:
        excluded = exclude or set()
        for port in range(SEARCH_PORT_START, SEARCH_PORT_END + 1):
            if port in excluded:
                continue
            with socket.socket() as listener:
                try:
                    listener.bind(("127.0.0.1", port))
                except OSError:
                    continue
                return port
        raise RuntimeError("no free loopback port is available for SearXNG")

    def _gateway_port(self) -> int:
        override = os.environ.get("HAMES_GATEWAY__PORT")
        if override is not None:
            try:
                return int(override)
            except ValueError:
                return 7411
        try:
            with self.paths.config_file.open("rb") as handle:
                config = tomllib.load(handle)
        except (FileNotFoundError, OSError, tomllib.TOMLDecodeError):
            return 7411
        gateway = config.get("gateway")
        if isinstance(gateway, dict):
            port = cast(dict[str, object], gateway).get("port")
            if isinstance(port, int) and 1 <= port <= 65535:
                return port
        return 7411

    def _container_exists(self, state: SearchSetupState) -> bool:
        result = self._run(
            [state.runtime, "inspect", state.container_name], timeout=10, check=False
        )
        if result.returncode != 0:
            return False
        label = self._inspect_value(state, f'{{{{ index .Config.Labels "{SERVICE_LABEL}" }}}}')
        home = self._inspect_value(state, f'{{{{ index .Config.Labels "{HOME_LABEL}" }}}}')
        if label != "search" or home != self._home_label():
            raise RuntimeError(
                f"container name is occupied by a non-Hames service: {state.container_name}"
            )
        return True

    def _container_running(self, state: SearchSetupState) -> bool:
        return self._inspect_value(state, "{{.State.Running}}") == "true"

    def _container_image(self, state: SearchSetupState) -> str:
        return self._inspect_value(state, "{{.Config.Image}}")

    def _inspect_value(self, state: SearchSetupState, template: str) -> str:
        result = self._run(
            [state.runtime, "inspect", "--format", template, state.container_name],
            timeout=10,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def _create_container(self, state: SearchSetupState, *, image: str | None = None) -> None:
        self._ensure_cache_volume(state)
        self._run(
            [
                state.runtime,
                "run",
                "--detach",
                "--name",
                state.container_name,
                "--label",
                f"{SERVICE_LABEL}=search",
                "--label",
                f"{HOME_LABEL}={self._home_label()}",
                "--restart",
                "unless-stopped",
                "--env",
                "FORCE_OWNERSHIP=false",
                "--security-opt",
                "no-new-privileges",
                "--publish",
                f"127.0.0.1:{state.port}:8080",
                "--volume",
                f"{self.paths.search_config}:/etc/searxng:ro",
                "--volume",
                f"{self._cache_volume_name()}:/var/cache/searxng:rw",
                image or state.image,
            ],
            timeout=60,
        )

    def _ensure_cache_volume(self, state: SearchSetupState) -> None:
        name = self._cache_volume_name()
        inspected = self._run([state.runtime, "volume", "inspect", name], timeout=10, check=False)
        if inspected.returncode == 0:
            service = self._volume_label(state, name, SERVICE_LABEL)
            home = self._volume_label(state, name, HOME_LABEL)
            if service != "search-cache" or home != self._home_label():
                raise RuntimeError(f"volume name is occupied by non-Hames data: {name}")
            return
        self._run(
            [
                state.runtime,
                "volume",
                "create",
                "--label",
                f"{SERVICE_LABEL}=search-cache",
                "--label",
                f"{HOME_LABEL}={self._home_label()}",
                name,
            ],
            timeout=20,
        )

    def _volume_label(self, state: SearchSetupState, name: str, label: str) -> str:
        result = self._run(
            [
                state.runtime,
                "volume",
                "inspect",
                "--format",
                f'{{{{ index .Labels "{label}" }}}}',
                name,
            ],
            timeout=10,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def _remove_container(self, state: SearchSetupState) -> None:
        if self._container_exists(state):
            self._run([state.runtime, "rm", "--force", state.container_name], timeout=30)

    def _stop_container(self, state: SearchSetupState, *, ignore_errors: bool) -> None:
        try:
            if self._container_exists(state) and self._container_running(state):
                self._run([state.runtime, "stop", "--time", "10", state.container_name], timeout=20)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            if not ignore_errors:
                raise

    def _healthy(self, state: SearchSetupState) -> bool:
        try:
            response = httpx.get(self.url(state), timeout=2, follow_redirects=False)
        except httpx.HTTPError:
            return False
        return 200 <= response.status_code < 500

    def _wait_healthy(self, state: SearchSetupState, *, seconds: float = 30.0) -> bool:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if self._healthy(state):
                return True
            time.sleep(0.2)
        return False

    @staticmethod
    def _run(
        command: list[str], *, timeout: float, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if check and result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            raise RuntimeError(f"{command[0]} {command[1]} failed: {detail}")
        return result


def _atomic_private_write(path: Path, content: str, *, mode: int = 0o600) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".hames-search-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(mode)
    finally:
        temporary.unlink(missing_ok=True)
