from __future__ import annotations

from pathlib import Path

import pytest

from hames.database import MIGRATIONS, Database
from hames.ledger import Ledger
from hames.paths import HamesPaths
from hames.plugins import PluginStore, inspect_package, load_manifest


def _write_plugin(root: Path, *, plugin_id: str = "project-stats") -> Path:
    package = root / plugin_id
    package.mkdir()
    (package / "plugin.toml").write_text(
        "\n".join(
            [
                f'id = "{plugin_id}"',
                'name = "Project Stats"',
                'version = "0.1.0"',
                "api_version = 1",
                'entrypoint = "worker.py"',
                'capabilities = ["tool"]',
                'permissions = ["broker:project_read"]',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (package / "worker.py").write_text("print('ok')\n", encoding="utf-8")
    return package


def test_plugin_schema_is_migration_eleven(tmp_path: Path) -> None:
    path = tmp_path / "m8.db"
    Database(path, migrations=MIGRATIONS[:10]).migrate()
    Database(path).migrate()
    assert len(MIGRATIONS) == 12
    with Database(path).connect() as connection:
        tables = {
            str(row["name"])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert {"plugins", "plugin_versions", "plugin_proposals"} <= tables


def test_manifest_rejects_unknown_api_and_permissions(tmp_path: Path) -> None:
    package = _write_plugin(tmp_path)
    (package / "plugin.toml").write_text(
        'id = "project-stats"\nname = "X"\nversion = "0.1.0"\n'
        'api_version = 99\nentrypoint = "worker.py"\ncapabilities = ["tool"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="api_version"):
        load_manifest(package / "plugin.toml")
    (package / "plugin.toml").write_text(
        'id = "project-stats"\nname = "X"\nversion = "0.1.0"\n'
        'api_version = 1\nentrypoint = "worker.py"\ncapabilities = ["tool"]\n'
        'permissions = ["broker:root"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="unknown plugin permission"):
        load_manifest(package / "plugin.toml")


def test_inspect_and_install_stay_disabled(hames_paths: HamesPaths, tmp_path: Path) -> None:
    hames_paths.ensure_foundation()
    package = _write_plugin(tmp_path)
    inspected = inspect_package(package)
    assert inspected.manifest.id == "project-stats"
    assert inspected.fingerprint
    second = inspect_package(package)
    assert second.fingerprint == inspected.fingerprint
    ledger = Ledger.open(hames_paths.database)
    session = ledger.create_session(
        working_directory=tmp_path, agent_id="default", provider="fake", model="fixture"
    )
    store = PluginStore(hames_paths, ledger)
    version = store.install(package, session_id=session.id, agent_id=session.agent_id)
    assert version.plugin_id == "project-stats"
    plugin = store.get("project-stats")
    assert plugin.enabled is False
    assert plugin.active_version_id == version.id
    again = store.install(package, session_id=session.id, agent_id=session.agent_id)
    assert again.id == version.id
    assert [item.id for item in store.list_plugins()] == ["project-stats"]
    types = [event.type for event in ledger.list_events(session.id)]
    assert types.count("plugin.installed") == 1
