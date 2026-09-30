"""Deterministic host diagnostics."""

from __future__ import annotations

import platform
import sqlite3
import sys
import tomllib

from pydantic import BaseModel, Field

from hames import PROTOCOL_VERSION, __version__
from hames.agent import load_agent
from hames.config import is_legacy_config, load_config
from hames.paths import HamesPaths
from hames.platform_support import (
    bubblewrap_path,
    core_platform_supported,
    isolation_available,
    macos_sandbox_path,
    sandbox_unavailable_reason,
)
from hames.search_service import SearchService, SearchStatus


class DoctorReport(BaseModel):
    healthy: bool
    version: str
    protocol_version: int
    python_version: str
    platform: str
    hames_home: str
    database_path: str
    sqlite_version: str
    sqlite_fts5: bool
    bubblewrap: bool
    macos_sandbox: bool = False
    limitations: list[str] = Field(default_factory=list)
    default_agent_hash: str
    config_compatibility: str | None
    search: SearchStatus


def _has_fts5() -> bool:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE probe USING fts5(value)")
    except sqlite3.OperationalError:
        return False
    finally:
        connection.close()
    return True


def run_doctor(paths: HamesPaths) -> DoctorReport:
    paths.ensure_foundation()
    load_config(paths)
    agent = load_agent(paths.default_agent)
    fts5 = _has_fts5()
    supported_python = sys.version_info >= (3, 12)
    supported_platform = core_platform_supported()
    return DoctorReport(
        healthy=supported_python and supported_platform and fts5,
        version=__version__,
        protocol_version=PROTOCOL_VERSION,
        python_version=platform.python_version(),
        platform=platform.platform(),
        hames_home=str(paths.root),
        database_path=str(paths.database),
        sqlite_version=sqlite3.sqlite_version,
        sqlite_fts5=fts5,
        bubblewrap=bubblewrap_path() is not None,
        macos_sandbox=macos_sandbox_path() is not None,
        limitations=[] if isolation_available() else [sandbox_unavailable_reason()],
        default_agent_hash=agent.content_hash,
        config_compatibility=_config_compatibility(paths),
        search=SearchService(paths).status(),
    )


def _config_compatibility(paths: HamesPaths) -> str | None:
    if not paths.config_file.exists():
        return None
    with paths.config_file.open("rb") as handle:
        value = tomllib.load(handle)
    if is_legacy_config(value):
        return "legacy config detected; compatible M0 provider fields are translated in memory"
    return None
