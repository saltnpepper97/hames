from __future__ import annotations

import subprocess
from pathlib import Path

from hames.environment import EnvironmentSnapshotter, render_environment_context


def _git(cwd: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *arguments], check=True, capture_output=True)


def test_snapshot_captures_host_and_refreshes_workspace_facts(tmp_path: Path) -> None:
    os_release = tmp_path / "os-release"
    os_release.write_text('ID=arch\nPRETTY_NAME="Arch Linux"\n', encoding="utf-8")
    repository = tmp_path / "project"
    repository.mkdir()
    _git(repository, "init", "-b", "main")
    _git(repository, "config", "user.name", "Hames Test")
    _git(repository, "config", "user.email", "hames@example.invalid")
    tracked = repository / "tracked.txt"
    tracked.write_text("clean\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-m", "initial")

    snapshots = EnvironmentSnapshotter(
        environ={
            "XDG_SESSION_TYPE": "wayland",
            "XDG_CURRENT_DESKTOP": "Halley:wlroots",
        },
        os_release_path=os_release,
    )
    clean = snapshots.capture(repository)

    assert clean.host.os_name == "Arch Linux"
    assert clean.host.os_id == "arch"
    assert clean.host.session_type == "wayland"
    assert clean.host.compositor == "Halley"
    assert clean.workspace.repository is True
    assert clean.workspace.repository_root == str(repository)
    assert clean.workspace.branch == "main"
    assert clean.workspace.dirty is False
    assert clean.workspace.changed_files == 0

    tracked.write_text("changed\n", encoding="utf-8")
    (repository / "new.txt").write_text("new\n", encoding="utf-8")
    dirty = snapshots.capture(repository)

    assert dirty.host is clean.host
    assert dirty.workspace.dirty is True
    assert dirty.workspace.changed_files == 2

    rendered = render_environment_context(dirty)
    assert "Arch Linux" in rendered
    assert "session wayland" in rendered
    assert "desktop/compositor Halley" in rendered
    assert "Git repository, main, dirty (2 changed paths)" in rendered
    assert "observed_at" not in rendered


def test_snapshot_reports_a_non_repository_without_failing(tmp_path: Path) -> None:
    snapshot = EnvironmentSnapshotter(environ={}).capture(tmp_path)

    assert snapshot.workspace.git_available is True
    assert snapshot.workspace.repository is False
    assert snapshot.workspace.dirty is None
    assert "not a Git repository" in render_environment_context(snapshot)
