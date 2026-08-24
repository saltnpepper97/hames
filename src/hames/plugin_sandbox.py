"""Bubblewrap launcher for plugin workers. Stricter than Skill scripts."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


class PluginSandboxError(RuntimeError):
    pass


def bwrap_available() -> bool:
    return shutil.which("bwrap") is not None


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
) -> list[str]:
    entry = package.joinpath(*Path(entrypoint).parts)
    if not bwrap_available():
        if not allow_unsandboxed:
            raise PluginSandboxError("plugin isolation is unavailable (bwrap missing)")
        interpreter = sys.executable
        if env_root is not None:
            candidate = env_root / "bin" / "python"
            if candidate.is_file():
                interpreter = str(candidate)
        return [interpreter, "-u", str(entry)]
    python = _sandbox_python()
    command = [
        shutil.which("bwrap") or "bwrap",
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
