from __future__ import annotations

import os
import plistlib
from pathlib import Path

import pytest

from hames import launchd
from hames.paths import HamesPaths


def test_launchd_install_writes_private_agent_for_selected_home(
    hames_paths: HamesPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "Library" / "LaunchAgents" / "io.hames.gateway.plist"
    monkeypatch.setattr(launchd.sys, "platform", "darwin")
    monkeypatch.setattr(launchd, "agent_path", lambda: path)
    bootstrapped: list[bool] = []
    monkeypatch.setattr(launchd, "bootstrap", lambda: bootstrapped.append(True))

    assert launchd.install(hames_paths) == path
    assert bootstrapped == [True]
    assert path.stat().st_mode & 0o777 == 0o600
    payload = plistlib.loads(path.read_bytes())
    assert payload["Label"] == launchd.LABEL
    assert payload["ProgramArguments"][-3:] == ["-m", "hames.cli", "serve"]
    assert payload["EnvironmentVariables"]["HAMES_HOME"] == str(hames_paths.root)
    assert payload["RunAtLoad"] and payload["KeepAlive"]
    assert launchd.manages(hames_paths)
    assert not launchd.manages(HamesPaths(tmp_path / "other-home"))
    with pytest.raises(FileExistsError):
        launchd.install(hames_paths)


def test_launchd_install_removes_plist_when_bootstrap_fails(
    hames_paths: HamesPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "io.hames.gateway.plist"
    monkeypatch.setattr(launchd.sys, "platform", "darwin")
    monkeypatch.setattr(launchd, "agent_path", lambda: path)

    def failed_bootstrap() -> None:
        raise OSError("launchd refused agent")

    monkeypatch.setattr(launchd, "bootstrap", failed_bootstrap)
    with pytest.raises(OSError, match="launchd refused agent"):
        launchd.install(hames_paths)
    assert not path.exists()


def test_launchd_is_ignored_on_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(launchd.sys, "platform", "linux")
    assert not launchd.installed()
    assert not launchd.manages(HamesPaths(Path(os.getcwd()) / "unused"))
