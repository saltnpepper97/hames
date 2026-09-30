from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from hames.macos_sandbox import isolated_command


@pytest.mark.skipif(sys.platform != "darwin", reason="requires native macOS sandbox")
def test_skill_sandbox_reads_project_and_writes_only_scratch(tmp_path: Path) -> None:
    package = tmp_path / "skill"
    package.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (project / "input.txt").write_text("project data", encoding="utf-8")
    with tempfile.TemporaryDirectory(dir=Path.home()) as secret_dir:
        secret = Path(secret_dir) / "secret.txt"
        secret.write_text("private", encoding="utf-8")
        script = package / "probe.py"
        script.write_text(
            f"""from pathlib import Path
print('project=' + Path({str(project / "input.txt")!r}).read_text())
Path('output.txt').write_text('scratch data')
for label, path in (
    ('project_write', Path({str(project / "blocked.txt")!r})),
    ('home_read', Path({str(secret)!r})),
):
    try:
        if label == 'project_write':
            path.write_text('blocked')
        else:
            path.read_text()
        print(label + '=allowed')
    except OSError:
        print(label + '=denied')
""",
            encoding="utf-8",
        )
        scratch = tmp_path / "scratch"
        command = isolated_command(
            executable=Path(sys.executable),
            arguments=[str(script)],
            package=package,
            scratch=scratch,
            project=project,
        )
        completed = subprocess.run(
            command, cwd=scratch, capture_output=True, text=True, check=False, timeout=15
        )
    assert completed.returncode == 0, completed.stderr
    assert "project=project data" in completed.stdout
    assert "project_write=denied" in completed.stdout
    assert "home_read=denied" in completed.stdout
    assert (scratch / "output.txt").read_text(encoding="utf-8") == "scratch data"
    assert not (project / "blocked.txt").exists()
