"""Bubblewrap launcher for plugin workers. Stricter than Skill scripts."""

from __future__ import annotations

import sys
from pathlib import Path

from hames.macos_sandbox import isolated_command
from hames.platform_support import (
    bubblewrap_path,
    macos_sandbox_path,
    sandbox_unavailable_reason,
)


class PluginSandboxError(RuntimeError):
    pass


def bwrap_available() -> bool:
    return bubblewrap_path() is not None


def sandbox_available() -> bool:
    return bwrap_available() or macos_sandbox_path() is not None


def _sandbox_python() -> str:
    for candidate in ("/usr/bin/python3", "/usr/bin/python"):
        if Path(candidate).is_file():
            return candidate
    raise PluginSandboxError("no python interpreter under /usr/bin")


def worker_command(
    *,
    package: Path,
    entrypoint: str,
    env_root: Path | None,
    allow_unsandboxed: bool,
    scratch: Path | None = None,
) -> list[str]:
    entry = package.joinpath(*Path(entrypoint).parts)
    if macos_sandbox_path() is not None:
        if scratch is None:
            raise PluginSandboxError("macOS plugin isolation requires a private scratch directory")
        interpreter = env_root / "bin" / "python" if env_root is not None else Path(sys.executable)
        return isolated_command(
            executable=interpreter,
            arguments=["-u", str(entry)],
            package=package,
            scratch=scratch,
            env_root=env_root,
        )
    if not bwrap_available():
        if not allow_unsandboxed:
            raise PluginSandboxError(sandbox_unavailable_reason())
        interpreter = sys.executable
        if env_root is not None:
            candidate = env_root / "bin" / "python"
            if candidate.is_file():
                interpreter = str(candidate)
        return [interpreter, "-u", str(entry)]
    python = _sandbox_python()
    command = [
        bubblewrap_path() or "bwrap",
        "--die-with-parent",
        "--new-session",
        "--unshare-all",
        "--ro-bind",
        "/usr",
        "/usr",
    ]
    for extra in ("/lib64", "/lib"):
        if Path(extra).exists():
            command.extend(["--ro-bind", extra, extra])
    command.extend(
        [
            "--ro-bind",
            "/etc",
            "/etc",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/home",
            "--ro-bind",
            str(package),
            "/plugin",
            "--chdir",
            "/plugin",
            "--clearenv",
            "--setenv",
            "PATH",
            "/usr/bin",
            "--setenv",
            "HOME",
            "/tmp",
            "--setenv",
            "PYTHONUNBUFFERED",
            "1",
            python,
            f"/plugin/{entrypoint}",
        ]
    )
    if env_root is not None and env_root.exists():
        insert_at = command.index("--chdir")
        command[insert_at:insert_at] = ["--ro-bind", str(env_root), "/plugin-env"]
    return command
