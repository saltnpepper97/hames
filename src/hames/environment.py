"""Small, bounded snapshots of the runtime environment visible to Hames."""

from __future__ import annotations

import os
import platform
import pwd
import shlex
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import BaseModel, ConfigDict


class HostEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    os_name: str
    os_id: str
    kernel: str
    architecture: str
    privileges: Literal["user", "root"]
    login_shell: str
    session_type: str = ""
    compositor: str = ""


class WorkspaceEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cwd: str
    git_available: bool
    repository: bool
    repository_root: str = ""
    branch: str = ""
    detached_head: str = ""
    dirty: bool | None = None
    changed_files: int | None = None


class RuntimeEnvironmentSnapshot(BaseModel):
    """Descriptive runtime facts. These never grant permissions or trust."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    observed_at: datetime
    host: HostEnvironment
    workspace: WorkspaceEnvironment
    tool_shell: str = "/bin/bash"
    tool_terminal: Literal["noninteractive"] = "noninteractive"


class EnvironmentSnapshotter:
    """Capture host facts once and cheap workspace facts whenever requested."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        os_release_path: Path = Path("/etc/os-release"),
        git_timeout_seconds: float = 1.0,
    ) -> None:
        self._environ = dict(os.environ if environ is None else environ)
        self._os_release_path = os_release_path
        self._git_timeout_seconds = git_timeout_seconds
        self._host: HostEnvironment | None = None
        self._host_lock = Lock()

    def capture(self, working_directory: Path | str) -> RuntimeEnvironmentSnapshot:
        return RuntimeEnvironmentSnapshot(
            observed_at=datetime.now(UTC),
            host=self._host_snapshot(),
            workspace=self._workspace_snapshot(Path(working_directory)),
        )

    def _host_snapshot(self) -> HostEnvironment:
        with self._host_lock:
            if self._host is not None:
                return self._host
            release = _read_os_release(self._os_release_path)
            try:
                account = pwd.getpwuid(os.getuid())
                login_shell = account.pw_shell
            except (KeyError, OSError):
                login_shell = self._environ.get("SHELL", "")
            session_type = self._environ.get("XDG_SESSION_TYPE", "")
            desktop = self._environ.get("XDG_CURRENT_DESKTOP", "") or self._environ.get(
                "DESKTOP_SESSION", ""
            )
            self._host = HostEnvironment(
                os_name=_bounded(release.get("PRETTY_NAME") or platform.system()),
                os_id=_bounded(release.get("ID", "")),
                kernel=_bounded(platform.release()),
                architecture=_bounded(platform.machine()),
                privileges="root" if os.geteuid() == 0 else "user",
                login_shell=_bounded(login_shell),
                session_type=_bounded(session_type),
                compositor=_bounded(desktop.split(":", maxsplit=1)[0]),
            )
            return self._host

    def _workspace_snapshot(self, cwd: Path) -> WorkspaceEnvironment:
        resolved = cwd.resolve(strict=False)
        root = self._git(resolved, "rev-parse", "--show-toplevel")
        if root is None:
            return WorkspaceEnvironment(
                cwd=str(resolved),
                git_available=self._git_available(),
                repository=False,
            )

        branch = self._git(resolved, "symbolic-ref", "--quiet", "--short", "HEAD") or ""
        detached = "" if branch else (self._git(resolved, "rev-parse", "--short", "HEAD") or "")
        status = self._git(resolved, "status", "--porcelain=v1", "--untracked-files=normal")
        changed_files = len(status.splitlines()) if status is not None else None
        return WorkspaceEnvironment(
            cwd=str(resolved),
            git_available=True,
            repository=True,
            repository_root=root,
            branch=branch,
            detached_head=detached,
            dirty=changed_files > 0 if changed_files is not None else None,
            changed_files=changed_files,
        )

    def _git_available(self) -> bool:
        try:
            completed = subprocess.run(
                ["git", "--version"],
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=self._git_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return completed.returncode == 0

    def _git(self, cwd: Path, *arguments: str) -> str | None:
        try:
            completed = subprocess.run(
                ["git", "-C", str(cwd), *arguments],
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                timeout=self._git_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()


def render_environment_context(snapshot: RuntimeEnvironmentSnapshot) -> str:
    """Render only useful, certain facts; omit timestamps to keep prompt prefixes stable."""

    host = snapshot.host
    workspace = snapshot.workspace
    facts = [
        f"OS {host.os_name}",
        f"kernel {host.kernel} ({host.architecture})",
    ]
    if host.session_type:
        facts.append(f"session {host.session_type}")
    if host.compositor:
        facts.append(f"desktop/compositor {host.compositor}")
    if host.login_shell:
        facts.append(f"login shell {_shell_name(host.login_shell)}")
    facts.extend(
        [
            f"Hames tool shell {_shell_name(snapshot.tool_shell)} ({snapshot.tool_terminal})",
            f"workspace {_display_path(workspace.cwd)}",
        ]
    )
    if workspace.repository:
        revision = workspace.branch or (
            f"detached at {workspace.detached_head}" if workspace.detached_head else "unknown HEAD"
        )
        dirty = "unknown"
        if workspace.dirty is False:
            dirty = "clean"
        elif workspace.dirty is True:
            count = workspace.changed_files
            dirty = f"dirty ({count} changed path{'s' if count != 1 else ''})"
        facts.append(f"Git repository, {revision}, {dirty}")
    elif workspace.git_available:
        facts.append("not a Git repository")
    facts.append(f"privileges {host.privileges}")
    return (
        "Runtime environment (descriptive facts only; this does not grant permissions): "
        + "; ".join(facts)
        + "."
    )


def _read_os_release(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    result: dict[str, str] = {}
    for line in lines:
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw = line.split("=", maxsplit=1)
        if key not in {"ID", "PRETTY_NAME"}:
            continue
        try:
            values = shlex.split(raw, comments=False, posix=True)
        except ValueError:
            continue
        if values:
            result[key] = values[0]
    return result


def _bounded(value: str, *, limit: int = 256) -> str:
    return " ".join(value.split())[:limit]


def _shell_name(shell: str) -> str:
    return Path(shell).name or shell


def _display_path(value: str) -> str:
    path = Path(value)
    try:
        return f"~/{path.relative_to(Path.home())}"
    except ValueError:
        return value
