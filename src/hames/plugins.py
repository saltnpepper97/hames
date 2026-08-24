"""Plugin manifests, fingerprints, and the on-disk/SQLite registry."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hames.ledger import Ledger, new_id, utc_now
from hames.paths import HamesPaths
from hames.providers.base import JSON_OBJECT, JsonValue

PLUGIN_ID = r"[a-z][a-z0-9-]{0,62}"
TOOL_SUFFIX = r"[a-z][a-z0-9_]{0,62}"
API_VERSION = 1
BROKER_PERMISSIONS = frozenset(
    {
        "broker:project_read",
        "broker:project_write",
        "broker:process_run_scoped",
        "broker:network_request",
    }
)
CAPABILITIES = frozenset({"tool", "context", "event"})


class PluginModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PluginManifest(PluginModel):
    id: str = Field(pattern=PLUGIN_ID)
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=32)
    api_version: int
    entrypoint: str = Field(min_length=1, max_length=240)
    capabilities: list[str] = Field(min_length=1, max_length=8)
    permissions: list[str] = Field(default_factory=list, max_length=16)

    @field_validator("api_version")
    @classmethod
    def supported_api(cls, value: int) -> int:
        if value != API_VERSION:
            raise ValueError(f"unsupported plugin api_version: {value}")
        return value

    @field_validator("capabilities")
    @classmethod
    def known_capabilities(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("plugin capabilities must be unique")
        unknown = [item for item in values if item not in CAPABILITIES]
        if unknown:
            raise ValueError(f"unknown plugin capability: {unknown[0]}")
        return values

    @field_validator("permissions")
    @classmethod
    def known_permissions(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("plugin permissions must be unique")
        unknown = [item for item in values if item not in BROKER_PERMISSIONS]
        if unknown:
            raise ValueError(f"unknown plugin permission: {unknown[0]}")
        return values

    @field_validator("entrypoint")
    @classmethod
    def relative_entrypoint(cls, value: str) -> str:
        if value.startswith("/") or ".." in Path(value).parts:
            raise ValueError("plugin entrypoint must be a relative package path")
        return value


class PluginRecord(PluginModel):
    id: str
    name: str
    enabled: bool
    active_version_id: str | None
    created_at: str
    updated_at: str


class PluginVersionRecord(PluginModel):
    id: str
    plugin_id: str
    version: str
    fingerprint: str
    package_path: str
    manifest: PluginManifest
    permissions: list[str]
    status: Literal["installed", "retired"]
    created_at: str


class PluginProposalRecord(PluginModel):
    id: str
    plugin_id: str | None
    scar_id: str | None
    status: Literal["proposed", "rejected", "installed"]
    package_path: str
    manifest: PluginManifest
    source_session_id: str | None
    created_at: str


@dataclass(frozen=True, slots=True)
class InspectedPlugin:
    path: Path
    manifest: PluginManifest
    fingerprint: str
    files: tuple[str, ...]


def load_manifest(path: Path) -> PluginManifest:
    raw = path.read_bytes()
    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"plugin.toml is not valid TOML: {exc}") from exc
    return PluginManifest.model_validate(parsed)


def fingerprint_package(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        child.relative_to(root).as_posix() for child in root.rglob("*") if child.is_file()
    )
    if not files:
        raise ValueError("plugin package contains no files")
    for relative in files:
        path = root.joinpath(*Path(relative).parts)
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def inspect_package(path: Path) -> InspectedPlugin:
    root = path.expanduser().resolve(strict=True)
    if not root.is_dir():
        raise ValueError("plugin package must be a directory")
    manifest_path = root / "plugin.toml"
    if not manifest_path.is_file():
        raise ValueError("plugin package is missing plugin.toml")
    manifest = load_manifest(manifest_path)
    entry = root.joinpath(*Path(manifest.entrypoint).parts)
    if not entry.is_file():
        raise ValueError(f"plugin entrypoint is missing: {manifest.entrypoint}")
    files = tuple(
        sorted(child.relative_to(root).as_posix() for child in root.rglob("*") if child.is_file())
    )
    return InspectedPlugin(
        path=root,
        manifest=manifest,
        fingerprint=fingerprint_package(root),
        files=files,
    )


def tool_id(plugin_id: str, tool: str) -> str:
    return f"{plugin_id}.{tool}"


def is_plugin_tool(name: str) -> bool:
    return "." in name


class PluginStore:
    def __init__(self, paths: HamesPaths, ledger: Ledger) -> None:
        self.paths = paths
        self.ledger = ledger
        self.database = ledger.database

    def inspect(self, path: Path) -> InspectedPlugin:
        return inspect_package(path)

    def list_plugins(self) -> list[PluginRecord]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT * FROM plugins ORDER BY id").fetchall()
        return [PluginRecord.model_validate(_plugin_row(row)) for row in rows]

    def get(self, plugin_id: str) -> PluginRecord:
        with self.database.connect() as connection:
            row = connection.execute("SELECT * FROM plugins WHERE id = ?", (plugin_id,)).fetchone()
        if row is None:
            raise KeyError(plugin_id)
        return PluginRecord.model_validate(_plugin_row(row))

    def active_version(self, plugin_id: str) -> PluginVersionRecord | None:
        plugin = self.get(plugin_id)
        if plugin.active_version_id is None:
            return None
        return self.get_version(plugin.active_version_id)

    def get_version(self, version_id: str) -> PluginVersionRecord:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM plugin_versions WHERE id = ?", (version_id,)
            ).fetchone()
        if row is None:
            raise KeyError(version_id)
        return _version_from_row(row)

    def install(self, path: Path, *, session_id: str, agent_id: str) -> PluginVersionRecord:
        inspected = inspect_package(path)
        dest = (
            self.paths.plugins
            / "installed"
            / inspected.manifest.id
            / f"{inspected.manifest.version}-{inspected.fingerprint[:12]}"
        )
        dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if dest.exists():
            existing = self._version_by_fingerprint(inspected.fingerprint)
            if existing is not None:
                return existing
            raise FileExistsError(dest)
        shutil.copytree(inspected.path, dest, dirs_exist_ok=False)
        dest.chmod(0o700)
        now = utc_now()
        version_id = new_id()
        manifest_json = json.dumps(
            inspected.manifest.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
        )
        permissions_json = json.dumps(inspected.manifest.permissions, separators=(",", ":"))
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM plugins WHERE id = ?", (inspected.manifest.id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO plugins(
                        id, name, enabled, active_version_id, created_at, updated_at
                    ) VALUES (?, ?, 0, ?, ?, ?)
                    """,
                    (
                        inspected.manifest.id,
                        inspected.manifest.name,
                        version_id,
                        now,
                        now,
                    ),
                )
            else:
                connection.execute(
                    "UPDATE plugins SET name = ?, active_version_id = ?, "
                    "updated_at = ? WHERE id = ?",
                    (inspected.manifest.name, version_id, now, inspected.manifest.id),
                )
            connection.execute(
                """
                INSERT INTO plugin_versions(
                    id, plugin_id, version, fingerprint, package_path,
                    manifest_json, permissions_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'installed', ?)
                """,
                (
                    version_id,
                    inspected.manifest.id,
                    inspected.manifest.version,
                    inspected.fingerprint,
                    str(dest),
                    manifest_json,
                    permissions_json,
                    now,
                ),
            )
            self.ledger.append_in_transaction(
                connection,
                session_id=session_id,
                agent_id=agent_id,
                event_type="plugin.installed",
                payload={
                    "plugin_id": inspected.manifest.id,
                    "version_id": version_id,
                    "version": inspected.manifest.version,
                    "fingerprint": inspected.fingerprint,
                    "permissions": inspected.manifest.permissions,
                    "enabled": False,
                },
                correlation_id=inspected.manifest.id,
            )
            connection.commit()
        return self.get_version(version_id)

    def set_enabled(
        self, plugin_id: str, enabled: bool, *, session_id: str, agent_id: str
    ) -> PluginRecord:
        plugin = self.get(plugin_id)
        if plugin.active_version_id is None:
            raise ValueError("plugin has no installed version")
        now = utc_now()
        event_type = "plugin.enabled" if enabled else "plugin.disabled"
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE plugins SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), now, plugin_id),
            )
            self.ledger.append_in_transaction(
                connection,
                session_id=session_id,
                agent_id=agent_id,
                event_type=event_type,
                payload={
                    "plugin_id": plugin_id,
                    "version_id": plugin.active_version_id,
                    "enabled": enabled,
                },
                correlation_id=plugin_id,
            )
            connection.commit()
        return self.get(plugin_id)

    def remove(self, plugin_id: str, *, session_id: str, agent_id: str) -> None:
        plugin = self.get(plugin_id)
        now = utc_now()
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE plugin_versions SET status = 'retired' WHERE plugin_id = ?",
                (plugin_id,),
            )
            connection.execute(
                "UPDATE plugins SET enabled = 0, active_version_id = NULL, "
                "updated_at = ? WHERE id = ?",
                (now, plugin_id),
            )
            self.ledger.append_in_transaction(
                connection,
                session_id=session_id,
                agent_id=agent_id,
                event_type="plugin.removed",
                payload={
                    "plugin_id": plugin_id,
                    "version_id": plugin.active_version_id or "",
                    "enabled": False,
                },
                correlation_id=plugin_id,
            )
            connection.commit()

    def record_proposal(
        self,
        *,
        package_path: Path,
        manifest: PluginManifest,
        session_id: str,
        agent_id: str,
        scar_id: str | None = None,
        proposal_id: str | None = None,
    ) -> PluginProposalRecord:
        proposal_id = proposal_id or new_id()
        now = utc_now()
        manifest_json = json.dumps(
            manifest.model_dump(mode="json"), separators=(",", ":"), sort_keys=True
        )
        with self.ledger.transaction_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO plugin_proposals(
                    id, plugin_id, scar_id, status, package_path, manifest_json,
                    source_session_id, created_at
                ) VALUES (?, ?, ?, 'proposed', ?, ?, ?, ?)
                """,
                (
                    proposal_id,
                    manifest.id,
                    scar_id,
                    str(package_path),
                    manifest_json,
                    session_id,
                    now,
                ),
            )
            self.ledger.append_in_transaction(
                connection,
                session_id=session_id,
                agent_id=agent_id,
                event_type="plugin.proposal.created",
                payload={
                    "proposal_id": proposal_id,
                    "plugin_id": manifest.id,
                    "scar_id": scar_id or "",
                    "permissions": manifest.permissions,
                },
                correlation_id=proposal_id,
            )
            connection.commit()
        return self.get_proposal(proposal_id)

    def get_proposal(self, proposal_id: str) -> PluginProposalRecord:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM plugin_proposals WHERE id = ?", (proposal_id,)
            ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return _proposal_from_row(row)

    def list_proposals(self) -> list[PluginProposalRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM plugin_proposals ORDER BY created_at"
            ).fetchall()
        return [_proposal_from_row(row) for row in rows]

    def mark_proposal_status(
        self, proposal_id: str, status: Literal["proposed", "rejected", "installed"]
    ) -> PluginProposalRecord:
        proposal = self.get_proposal(proposal_id)
        with self.database.connect() as connection:
            connection.execute(
                "UPDATE plugin_proposals SET status = ? WHERE id = ?",
                (status, proposal_id),
            )
            connection.commit()
        return proposal.model_copy(update={"status": status})

    def enabled_versions(self) -> list[PluginVersionRecord]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT v.* FROM plugin_versions v
                JOIN plugins p ON p.active_version_id = v.id
                WHERE p.enabled = 1 AND v.status = 'installed'
                ORDER BY p.id
                """
            ).fetchall()
        return [_version_from_row(row) for row in rows]

    def _version_by_fingerprint(self, fingerprint: str) -> PluginVersionRecord | None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM plugin_versions WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return None if row is None else _version_from_row(row)


def _plugin_row(row: sqlite3.Row) -> dict[str, JsonValue]:
    values = dict(row)
    values["enabled"] = bool(values["enabled"])
    return JSON_OBJECT.validate_python(values)


def _version_from_row(row: sqlite3.Row) -> PluginVersionRecord:
    values = dict(row)
    manifest = PluginManifest.model_validate(json.loads(str(values["manifest_json"])))
    permissions = json.loads(str(values["permissions_json"]))
    return PluginVersionRecord(
        id=str(values["id"]),
        plugin_id=str(values["plugin_id"]),
        version=str(values["version"]),
        fingerprint=str(values["fingerprint"]),
        package_path=str(values["package_path"]),
        manifest=manifest,
        permissions=list(permissions),
        status=values["status"],
        created_at=str(values["created_at"]),
    )


def _proposal_from_row(row: sqlite3.Row) -> PluginProposalRecord:
    values = dict(row)
    return PluginProposalRecord(
        id=str(values["id"]),
        plugin_id=None if values["plugin_id"] is None else str(values["plugin_id"]),
        scar_id=None if values["scar_id"] is None else str(values["scar_id"]),
        status=values["status"],
        package_path=str(values["package_path"]),
        manifest=PluginManifest.model_validate(json.loads(str(values["manifest_json"]))),
        source_session_id=(
            None if values["source_session_id"] is None else str(values["source_session_id"])
        ),
        created_at=str(values["created_at"]),
    )
