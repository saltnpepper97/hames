"""Optional per-user launchd supervision for the macOS gateway."""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast

from hames.paths import HamesPaths

LABEL = "io.hames.gateway"


def agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def installed() -> bool:
    return sys.platform == "darwin" and agent_path().is_file()


def manages(paths: HamesPaths) -> bool:
    if not installed():
        return False
    try:
        raw: object = plistlib.loads(agent_path().read_bytes())
    except (OSError, ValueError):
        return False
    if not isinstance(raw, dict):
        return False
    payload = cast(dict[str, object], raw)
    environment = payload.get("EnvironmentVariables")
    return (
        payload.get("Label") == LABEL
        and isinstance(environment, dict)
        and cast(dict[str, object], environment).get("HAMES_HOME") == str(paths.root)
    )


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/launchctl", *args], capture_output=True, text=True, check=check, timeout=20
    )


def loaded() -> bool:
    return installed() and _run("print", f"{_domain()}/{LABEL}", check=False).returncode == 0


def bootstrap() -> None:
    if not installed():
        raise RuntimeError("Hames LaunchAgent is not installed")
    if loaded():
        _run("kickstart", "-k", f"{_domain()}/{LABEL}")
    else:
        _run("bootstrap", _domain(), str(agent_path()))


def bootout() -> None:
    if loaded():
        _run("bootout", f"{_domain()}/{LABEL}")


def install(paths: HamesPaths) -> Path:
    if sys.platform != "darwin":
        raise RuntimeError("launchd is only available on macOS")
    path = agent_path()
    if path.exists():
        raise FileExistsError(f"LaunchAgent already exists: {path}")
    paths.ensure_foundation()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    payload = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, "-m", "hames.cli", "serve"],
        "EnvironmentVariables": {
            "HAMES_HOME": str(paths.root),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        },
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(paths.logs / "launchd.stdout.log"),
        "StandardErrorPath": str(paths.logs / "launchd.stderr.log"),
        "Umask": 0o077,
    }
    data = plistlib.dumps(payload)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".hames-", delete=False) as handle:
        temporary = Path(handle.name)
        os.fchmod(handle.fileno(), 0o600)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)
    try:
        bootstrap()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def remove() -> None:
    if not installed():
        return
    bootout()
    agent_path().unlink()
