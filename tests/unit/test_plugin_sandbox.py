from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from hames.plugin_sandbox import PluginSandboxError, bwrap_available, worker_command


def test_missing_bwrap_refuses_untrusted_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_bwrap(_name: str) -> str | None:
        return None

    monkeypatch.setattr("hames.plugin_sandbox.shutil.which", no_bwrap)
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "worker.py").write_text("print('x')\n", encoding="utf-8")
    with pytest.raises(PluginSandboxError, match="bwrap missing"):
        worker_command(
            package=package,
            entrypoint="worker.py",
            env_root=None,
            allow_unsandboxed=False,
        )
    command = worker_command(
        package=package,
        entrypoint="worker.py",
        env_root=None,
        allow_unsandboxed=True,
    )
    assert command[-1].endswith("worker.py")


@pytest.mark.skipif(not bwrap_available(), reason="bwrap is not installed")
def test_sandbox_does_not_expose_real_home(tmp_path: Path) -> None:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "probe.py").write_text(
        "import os\nprint('HOME=' + os.environ.get('HOME', ''))\n",
        encoding="utf-8",
    )
    command = worker_command(
        package=package,
        entrypoint="probe.py",
        env_root=None,
        allow_unsandboxed=False,
    )
    completed = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
    assert completed.returncode == 0, completed.stderr
    assert "HOME=/tmp" in completed.stdout
    assert str(Path.home()) not in completed.stdout
