from __future__ import annotations

from pathlib import Path
from subprocess import CompletedProcess

import pytest

from hames.database import MIGRATIONS, Database
from hames.ledger import Ledger
from hames.paths import HamesPaths
from hames.workspaces import WorkspaceRegistry


def test_registry_does_not_expose_session_directories_until_authorized(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    ledger = Ledger.open(hames_paths.database)
    ledger.create_session(
        working_directory=project,
        agent_id="default",
        provider="fake",
        model="fixture",
    )

    registry = WorkspaceRegistry(ledger.database)
    assert registry.list() == []

    workspace = registry.register(project, touch=False)
    assert [(item.title, item.path) for item in registry.list()] == [("project", str(project))]
    assert registry.delete(workspace.id) is True
    assert project.is_dir()
    assert ledger.list_sessions()[0].working_directory == str(project)


def test_migration_hides_automatic_rows_until_the_path_is_reauthorized(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "legacy.db"
    project = tmp_path / "legacy-project"
    project.mkdir()
    old_database = Database(database_path, migrations=MIGRATIONS[:21])
    old_database.migrate()
    with old_database.connect() as connection:
        connection.execute(
            """
            INSERT INTO workspaces(id, path, title, created_at, updated_at)
            VALUES ('legacy-workspace', ?, 'Legacy project', ?, ?)
            """,
            (str(project), "2026-09-01T12:00:00Z", "2026-09-01T12:00:00Z"),
        )

    database = Database(database_path)
    database.migrate()
    registry = WorkspaceRegistry(database)
    assert registry.list() == []

    authorized = registry.register(project, touch=False)
    assert authorized.id == "legacy-workspace"
    assert [workspace.id for workspace in registry.list()] == ["legacy-workspace"]


def test_registry_canonicalizes_renames_and_creates_one_child(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    database = Database(hames_paths.database)
    database.migrate()
    registry = WorkspaceRegistry(database)
    project = tmp_path / "project"
    project.mkdir()

    first = registry.register(project / ".." / "project")
    same = registry.register(project)
    renamed = registry.rename(first.id, "Hames project")
    created = registry.create_directory(tmp_path, "new-project")

    assert same.id == first.id
    assert renamed.title == "Hames project"
    assert created.path == str(tmp_path / "new-project")
    assert (tmp_path / "new-project").is_dir()
    with pytest.raises(ValueError, match="one non-empty path segment"):
        registry.create_directory(tmp_path, "nested/project")


def test_directory_listing_returns_only_folders(hames_paths: HamesPaths, tmp_path: Path) -> None:
    database = Database(hames_paths.database)
    database.migrate()
    registry = WorkspaceRegistry(database)
    browse = tmp_path / "browse"
    browse.mkdir()
    (browse / "alpha").mkdir()
    (browse / "zeta").mkdir()
    (browse / "file.txt").write_text("not a directory", encoding="utf-8")

    listing = registry.list_directory(browse)

    assert listing.path == str(browse)
    assert [entry.name for entry in listing.directories] == ["alpha", "zeta"]


def test_native_picker_registers_selected_folder(
    hames_paths: HamesPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(hames_paths.database)
    database.migrate()
    registry = WorkspaceRegistry(database)
    selected = tmp_path / "selected"
    selected.mkdir()

    def find_zenity(_: str) -> str:
        return "/usr/bin/zenity"

    def select_folder(args: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(args, 0, f"{selected}\n", "")

    monkeypatch.setattr("hames.workspaces.shutil.which", find_zenity)
    monkeypatch.setattr(
        "hames.workspaces.subprocess.run",
        select_folder,
    )

    workspace = registry.pick_directory(tmp_path)

    assert workspace is not None
    assert workspace.path == str(selected)
    assert registry.list()[0].id == workspace.id


def test_picker_refreshes_graphical_login_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from hames.workspaces import _picker_environment  # pyright: ignore[reportPrivateUsage]

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)

    def find_systemctl(_: str) -> str:
        return "/usr/bin/systemctl"

    def show_environment(args: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(
            args, 0, "WAYLAND_DISPLAY=wayland-1\nDISPLAY=:0\nPATH=untrusted\n", ""
        )

    monkeypatch.setattr("hames.workspaces.shutil.which", find_systemctl)
    monkeypatch.setattr("hames.workspaces.subprocess.run", show_environment)
    environment = _picker_environment()
    assert environment["WAYLAND_DISPLAY"] == "wayland-1"
    assert environment["DISPLAY"] == ":0"
    assert environment.get("PATH") != "untrusted"


@pytest.mark.parametrize("stderr", ["", "Gtk-WARNING: cannot open display:"])
def test_picker_distinguishes_cancel_from_display_failure(
    hames_paths: HamesPaths, monkeypatch: pytest.MonkeyPatch, stderr: str
) -> None:
    database = Database(hames_paths.database)
    database.migrate()
    registry = WorkspaceRegistry(database)

    def find_zenity(_: str) -> str:
        return "/usr/bin/zenity"

    def empty_environment() -> dict[str, str]:
        return {}

    def cancel_or_fail(args: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(args, 1, "", stderr)

    monkeypatch.setattr("hames.workspaces.shutil.which", find_zenity)
    monkeypatch.setattr("hames.workspaces._picker_environment", empty_environment)
    monkeypatch.setattr("hames.workspaces.subprocess.run", cancel_or_fail)
    if stderr:
        with pytest.raises(RuntimeError, match="could not open"):
            registry.pick_directory()
    else:
        assert registry.pick_directory() is None
    assert registry.list() == []
