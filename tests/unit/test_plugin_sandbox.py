from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from hames.platform_support import macos_sandbox_path
from hames.plugin_sandbox import PluginSandboxError, bwrap_available, worker_command


def test_missing_bwrap_refuses_untrusted_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_bwrap(_name: str) -> str | None:
        return None

    monkeypatch.setattr("hames.platform_support.sys.platform", "linux")
    monkeypatch.setattr("hames.platform_support.shutil.which", no_bwrap)
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


@pytest.mark.skipif(sys.platform != "darwin", reason="requires native macOS sandbox")
def test_macos_sandbox_runs_worker_but_denies_home_and_network(tmp_path: Path) -> None:
    assert macos_sandbox_path() is not None
    package = tmp_path / "pkg"
    package.mkdir()
    with tempfile.TemporaryDirectory(dir=Path.home()) as secret_dir:
        secret = Path(secret_dir) / "secret"
        secret.write_text("private", encoding="utf-8")
        (package / "probe.py").write_text(
            """import os
import socket
from pathlib import Path
print('HOME=' + os.environ.get('HOME', ''))
for label, probe in (
    ('secret', lambda: Path(SECRET_PATH).read_text()),
    ('network', lambda: socket.socket().bind(('127.0.0.1', 0))),
):
    try:
        probe()
        print(label + '=allowed')
    except OSError:
        print(label + '=denied')
""".replace("SECRET_PATH", repr(str(secret))),
            encoding="utf-8",
        )
        scratch = tmp_path / "scratch"
        command = worker_command(
            package=package,
            entrypoint="probe.py",
            env_root=None,
            allow_unsandboxed=False,
            scratch=scratch,
        )
        completed = subprocess.run(
            command,
            cwd=package,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    assert completed.returncode == 0, completed.stderr
    assert f"HOME={scratch.resolve()}" in completed.stdout
    assert "secret=denied" in completed.stdout
    assert "network=denied" in completed.stdout
