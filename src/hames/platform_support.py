"""Host capabilities shared by diagnostics and isolated execution."""

from __future__ import annotations

import shutil
import sys


def core_platform_supported() -> bool:
    return sys.platform.startswith("linux") or sys.platform == "darwin"


def bubblewrap_path() -> str | None:
    if not sys.platform.startswith("linux"):
        return None
    return shutil.which("bwrap")


def sandbox_unavailable_reason() -> str:
    if not sys.platform.startswith("linux"):
        return (
            "isolated plugins and Skill scripts require Linux; "
            "sandbox support is not available on this platform"
        )
    return "isolation is unavailable (bwrap missing)"
