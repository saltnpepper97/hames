"""Deterministic host diagnostics."""

from __future__ import annotations

import platform
import shutil
import sqlite3
import sys
import tomllib

from pydantic import BaseModel

from hames import PROTOCOL_VERSION, __version__
from hames.agent import load_agent
from hames.config import is_legacy_config, load_config
from hames.paths import HamesPaths
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
    supported_platform = sys.platform.startswith("linux")
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
        bubblewrap=shutil.which("bwrap") is not None,
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
