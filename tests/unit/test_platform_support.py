import sys
from pathlib import Path

import pytest

from hames.doctor import run_doctor
from hames.paths import HamesPaths
from hames.platform_support import bubblewrap_path, core_platform_supported
from hames.plugin_sandbox import PluginSandboxError, worker_command


def test_macos_core_is_healthy_but_missing_sandbox_is_unavailable(
    hames_paths: HamesPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Even a binary named bwrap must not enable Linux isolation on macOS.
    monkeypatch.setattr("hames.platform_support.sys.platform", "darwin")

    def find_binary(_name: str) -> str:
        return "/opt/homebrew/bin/bwrap"

    monkeypatch.setattr("hames.platform_support.shutil.which", find_binary)
    monkeypatch.setattr("hames.platform_support.macos_sandbox_path", lambda: None)
    monkeypatch.setattr("hames.plugin_sandbox.macos_sandbox_path", lambda: None)
    assert core_platform_supported()
    assert bubblewrap_path() is None
    report = run_doctor(hames_paths)
    assert report.healthy
    assert not report.bubblewrap
    assert "sandbox-exec missing" in report.limitations[0]
    with pytest.raises(PluginSandboxError, match="sandbox-exec missing"):
        worker_command(
            package=Path("/unused"), entrypoint="worker.py", env_root=None, allow_unsandboxed=False
        )


def test_unsupported_platform_does_not_report_healthy(
    hames_paths: HamesPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("hames.platform_support.sys.platform", "freebsd14")
    assert not core_platform_supported()
    assert not run_doctor(hames_paths).healthy


@pytest.mark.asyncio
async def test_macos_skill_validation_and_execution_do_not_run_scripts(
    hames_paths: HamesPaths, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hames.blobs import BlobStore
    from hames.config import ToolsConfig
    from hames.gateway import GatewayState
    from hames.skills import SkillDraft, SkillScript
    from hames.tools import ToolContext

    state = GatewayState.create(hames_paths, providers={})
    session = state.ledger.create_session(
        working_directory=tmp_path, agent_id="default", provider="fake", model="fixture"
    )
    evidence = state.ledger.append(
        session_id=session.id, event_type="user.message", payload={"content": "test Skill"}
    )
    marker = tmp_path / "must-not-exist"
    script = SkillScript(
        id="probe", path="scripts/probe.py", interpreter="python", description="Sandbox probe"
    )
    version = state.skills.registry.create_draft(
        session=session,
        draft=SkillDraft(
            id="sandbox-probe",
            name="Sandbox probe",
            description="Test platform isolation",
            instructions="Run the probe in isolation.",
            scripts=[script],
            files={
                "scripts/probe.py": f"from pathlib import Path\nPath({str(marker)!r}).touch()\n"
            },
        ),
        evidence_event_ids=[evidence.id],
        created_by="user",
        run_id=None,
        causation_id=evidence.id,
    ).version
    monkeypatch.setattr("hames.platform_support.sys.platform", "darwin")
    monkeypatch.setattr("hames.platform_support.macos_sandbox_path", lambda: None)
    monkeypatch.setattr("hames.skill_runtime.macos_sandbox_path", lambda: None)
    monkeypatch.setattr("hames.runtime.macos_sandbox_path", lambda: None)
    try:
        validation = state.skills._validate(version)  # pyright: ignore[reportPrivateUsage]
        assert not validation["passed"]
        assert "sandbox-exec missing" in validation["script_checks"][0]["error"]
        result = await state.runs._execute_skill_script(  # pyright: ignore[reportPrivateUsage]
            version,
            script.path,
            script.interpreter,
            [],
            ToolContext(
                project_root=tmp_path,
                scratch_root=tmp_path / "scratch",
                blobs=BlobStore(tmp_path / "blobs"),
                config=ToolsConfig(),
            ),
        )
        assert result.status == "rejected"
        assert "sandbox-exec missing" in result.summary
        assert not marker.exists()
    finally:
        await state.runs.close()


@pytest.mark.skipif(sys.platform != "darwin", reason="requires native macOS sandbox")
@pytest.mark.asyncio
async def test_macos_skill_validation_and_runtime_execute_inside_sandbox(
    hames_paths: HamesPaths, tmp_path: Path
) -> None:
    from hames.blobs import BlobStore
    from hames.config import ToolsConfig
    from hames.gateway import GatewayState
    from hames.skills import SkillDraft, SkillScript
    from hames.tools import ToolContext

    state = GatewayState.create(hames_paths, providers={})
    session = state.ledger.create_session(
        working_directory=tmp_path, agent_id="default", provider="fake", model="fixture"
    )
    evidence = state.ledger.append(
        session_id=session.id, event_type="user.message", payload={"content": "test Skill"}
    )
    script = SkillScript(
        id="probe", path="scripts/probe.py", interpreter="python", description="Sandbox probe"
    )
    version = state.skills.registry.create_draft(
        session=session,
        draft=SkillDraft(
            id="mac-sandbox-probe",
            name="Mac sandbox probe",
            description="Exercise native Skill isolation",
            instructions="Run the probe in isolation.",
            scripts=[script],
            files={
                "scripts/probe.py": """from pathlib import Path
Path('result.txt').write_text('isolated')
""",
            },
        ),
        evidence_event_ids=[evidence.id],
        created_by="user",
        run_id=None,
        causation_id=evidence.id,
    ).version
    try:
        validation = state.skills._validate(version)  # pyright: ignore[reportPrivateUsage]
        assert validation["passed"], validation
        scratch = tmp_path / "scratch"
        result = await state.runs._execute_skill_script(  # pyright: ignore[reportPrivateUsage]
            version,
            script.path,
            script.interpreter,
            [],
            ToolContext(
                project_root=tmp_path,
                scratch_root=scratch,
                blobs=BlobStore(tmp_path / "blobs"),
                config=ToolsConfig(),
            ),
        )
        assert result.status == "completed", result
        assert (scratch / "result.txt").read_text(encoding="utf-8") == "isolated"
    finally:
        await state.runs.close()
