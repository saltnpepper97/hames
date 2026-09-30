"""Host capabilities shared by diagnostics and isolated execution."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path


def core_platform_supported() -> bool:
    return sys.platform.startswith("linux") or sys.platform == "darwin"


def bubblewrap_path() -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    return shutil.which("bwrap")


def macos_sandbox_path() -> str | None:
    if sys.platform != "darwin":
        return None
    path = Path("/usr/bin/sandbox-exec")
    return str(path) if path.is_file() else None


def isolation_available() -> bool:
    return bubblewrap_path() is not None or macos_sandbox_path() is not None


def sandbox_unavailable_reason() -> str:
    if sys.platform == "darwin":
        return "isolation is unavailable (macOS sandbox-exec missing)"
    if not sys.platform.startswith("linux"):
        return "isolated plugins and Skill scripts are unsupported on this platform"
    return "isolation is unavailable (bwrap missing)"
