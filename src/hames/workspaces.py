"""Durable host-side directory registrations for Hames clients."""

from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from hames.database import Database


def _picker_environment() -> dict[str, str]:
    """Refresh desktop variables for gateways started before graphical login."""
    environment = dict(os.environ)
    systemctl = shutil.which("systemctl")
    if systemctl:
        try:
            result = subprocess.run(
                [systemctl, "--user", "show-environment"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    key, separator, value = line.partition("=")
                    if separator and key in {
                        "DISPLAY",
                        "WAYLAND_DISPLAY",
                        "XAUTHORITY",
                        "XDG_RUNTIME_DIR",
                        "DBUS_SESSION_BUS_ADDRESS",
                    }:
                        environment[key] = value
        except (OSError, subprocess.TimeoutExpired):
            pass
    return environment


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class Workspace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    path: str
    title: str
    created_at: str
    updated_at: str
    available: bool


class DirectoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    path: str


class DirectoryListing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    parent: str | None
    directories: list[DirectoryEntry]


class NativeDirectoryPickerUnavailable(RuntimeError):
    """Raised when the host has no supported graphical folder picker."""


class WorkspaceRegistry:
    """Own named directory registrations without owning their files or sessions."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._write_lock = threading.Lock()

    @staticmethod
    def _canonical(path: Path) -> Path:
        canonical = path.expanduser().resolve(strict=True)
        if not canonical.is_dir():
            raise ValueError("workspace path must be a directory")
        return canonical

    @staticmethod
    def _title(value: str | None, path: Path) -> str:
        title = value.strip() if value is not None else path.name
        if not title:
            title = str(path)
        if len(title) > 160:
            raise ValueError("workspace title must be at most 160 characters")
        return title

    @staticmethod
    def _from_row(row: sqlite3.Row) -> Workspace:
        values = dict(row)
        values.pop("authorized", None)
        path = Path(str(values["path"]))
        return Workspace(**values, available=path.is_dir())

    def register(self, path: Path, *, title: str | None = None, touch: bool = True) -> Workspace:
        canonical = self._canonical(path)
        display_title = self._title(title, canonical)
        now = _utc_now()
        with self._write_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM workspaces WHERE path = ?", (str(canonical),)
            ).fetchone()
            if existing is None:
                workspace_id = str(uuid4())
                connection.execute(
                    """
                    INSERT INTO workspaces(
                        id, path, title, created_at, updated_at, authorized
                    ) VALUES (?, ?, ?, ?, ?, 1)
                    """,
                    (workspace_id, str(canonical), display_title, now, now),
                )
            else:
                workspace_id = str(existing["id"])
                if title is not None:
                    connection.execute(
                        """
                        UPDATE workspaces
                        SET title = ?, updated_at = ?, authorized = 1
                        WHERE id = ?
                        """,
                        (display_title, now, workspace_id),
                    )
                elif touch:
                    connection.execute(
                        """
                        UPDATE workspaces
                        SET updated_at = ?, authorized = 1
                        WHERE id = ?
                        """,
                        (now, workspace_id),
                    )
                else:
                    connection.execute(
                        "UPDATE workspaces SET authorized = 1 WHERE id = ?",
                        (workspace_id,),
                    )
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
            connection.commit()
        if row is None:
            raise RuntimeError("workspace registration disappeared")
        return self._from_row(row)

    def list(self) -> list[Workspace]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM workspaces
                WHERE authorized = 1
                ORDER BY updated_at DESC, created_at DESC
                """
            ).fetchall()
        return [self._from_row(row) for row in rows]

    def get(self, workspace_id: str) -> Workspace:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id = ? AND authorized = 1", (workspace_id,)
            ).fetchone()
        if row is None:
            raise KeyError(workspace_id)
        return self._from_row(row)

    def rename(self, workspace_id: str, title: str) -> Workspace:
        with self._write_lock, self.database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM workspaces WHERE id = ? AND authorized = 1", (workspace_id,)
            ).fetchone()
            if row is None:
                raise KeyError(workspace_id)
            display_title = self._title(title, Path(str(row["path"])))
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE workspaces SET title = ?, updated_at = ? WHERE id = ?",
                (display_title, _utc_now(), workspace_id),
            )
            updated = connection.execute(
                "SELECT * FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
            connection.commit()
        if updated is None:
            raise RuntimeError("workspace registration disappeared")
        return self._from_row(updated)

    def delete(self, workspace_id: str) -> bool:
        with self._write_lock, self.database.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "DELETE FROM workspaces WHERE id = ? AND authorized = 1", (workspace_id,)
            )
            connection.commit()
        return cursor.rowcount > 0

    def list_directory(self, path: Path | None = None) -> DirectoryListing:
        directory = self._canonical(path or Path.home())
        entries: list[DirectoryEntry] = []
        try:
            children = sorted(directory.iterdir(), key=lambda child: child.name.casefold())
        except PermissionError as exc:
            raise ValueError("directory cannot be read") from exc
        for child in children:
            try:
                if child.is_dir():
                    entries.append(DirectoryEntry(name=child.name, path=str(child.resolve())))
            except OSError:
                continue
        parent = None if directory.parent == directory else str(directory.parent)
        return DirectoryListing(path=str(directory), parent=parent, directories=entries)

    def create_directory(self, parent: Path, name: str) -> Workspace:
        directory = self._canonical(parent)
        child_name = name.strip()
        if (
            not child_name
            or child_name in {".", ".."}
            or "/" in child_name
            or "\\" in child_name
            or "\0" in child_name
        ):
            raise ValueError("folder name must be one non-empty path segment")
        child = directory / child_name
        try:
            child.mkdir()
        except FileExistsError as exc:
            raise FileExistsError("a file or folder with that name already exists") from exc
        return self.register(child)

    def pick_directory(self, initial: Path | None = None) -> Workspace | None:
        """Open the host's native folder chooser and register its selection."""
        selected = self.select_directory(initial)
        return self.register(selected, touch=False) if selected is not None else None

    def select_directory(self, initial: Path | None = None) -> Path | None:
        """Select an existing host folder without registering a workspace."""
        initial_directory = self._canonical(initial) if initial is not None else Path.home()
        zenity = shutil.which("zenity")
        kdialog = shutil.which("kdialog")
        if zenity:
            command = [
                zenity,
                "--file-selection",
                "--directory",
                "--title=Choose a folder for Hames",
                f"--filename={initial_directory}/",
            ]
        elif kdialog:
            command = [
                kdialog,
                "--getexistingdirectory",
                str(initial_directory),
                "--title",
                "Choose a folder for Hames",
            ]
        else:
            raise NativeDirectoryPickerUnavailable(
                "No supported native folder picker is installed (zenity or kdialog)."
            )

        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=600,
                env=_picker_environment(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise NativeDirectoryPickerUnavailable(
                "The native folder picker could not be opened."
            ) from exc
        if completed.returncode == 1:
            # GTK also uses exit 1 when it cannot connect to the desktop.
            detail = completed.stderr.strip()
            if any(
                marker in detail.lower()
                for marker in (
                    "cannot open display",
                    "could not connect to display",
                    "failed to connect to",
                    "no display",
                )
            ):
                raise RuntimeError(f"The system folder picker could not open: {detail}")
            return None
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "The native folder picker closed unexpectedly."
            raise RuntimeError(detail)
        selected = completed.stdout.strip()
        if not selected:
            return None
        return self._canonical(Path(selected))
