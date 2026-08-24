from __future__ import annotations

from pathlib import Path

import pytest

from hames.blobs import BlobStore
from hames.config import ToolsConfig
from hames.control import ControlStore
from hames.database import Database
from hames.policy import PolicyDecisionKind, PolicyGate, approval_request_hash
from hames.providers.base import JsonValue
from hames.tools import (
    EditFileArguments,
    EditFileTool,
    MemoryEditArguments,
    MemoryForgetArguments,
    ReadFileArguments,
    ReadFileTool,
    ScarControlArguments,
    ShellArguments,
    ShellTool,
    SkillControlArguments,
    ToolContext,
    ToolRegistry,
    WriteFileArguments,
    WriteFileTool,
)


def tool_context(tmp_path: Path, **config: object) -> ToolContext:
    project = tmp_path / "project"
    project.mkdir(exist_ok=True)
    return ToolContext(
        project_root=project,
        scratch_root=tmp_path / "scratch",
        blobs=BlobStore(tmp_path / "blobs"),
        config=ToolsConfig.model_validate(config),
    )


@pytest.mark.asyncio
async def test_read_write_and_exact_edit_are_deterministic(tmp_path: Path) -> None:
    context = tool_context(tmp_path)
    written = await WriteFileTool().execute(
        context,
        WriteFileArguments(path="src/value.txt", content="alpha\nbeta\n", create_parents=True),
    )
    assert written.status == "completed"
    assert written.structured_data["created"] is True

    read = await ReadFileTool().execute(
        context, ReadFileArguments(path="src/value.txt", start_line=2, end_line=2)
    )
    assert read.content == "beta\n"

    edited = await EditFileTool().execute(
        context,
        EditFileArguments(path="src/value.txt", old_text="beta", new_text="gamma"),
    )
    assert edited.status == "completed"
    assert "-beta" in edited.content
    assert "+gamma" in edited.content

    ambiguous_path = context.project_root / "ambiguous.txt"
    ambiguous_path.write_text("same same", encoding="utf-8")
    ambiguous = await EditFileTool().execute(
        context,
        EditFileArguments(path="ambiguous.txt", old_text="same", new_text="changed"),
    )
    assert ambiguous.status == "failed"
    assert "found 2" in ambiguous.summary
    assert ambiguous_path.read_text(encoding="utf-8") == "same same"


@pytest.mark.asyncio
async def test_paths_cannot_escape_and_large_results_use_blobs(tmp_path: Path) -> None:
    context = tool_context(tmp_path, model_result_char_limit=1024)
    (context.project_root / "large.txt").write_text("x" * 2048, encoding="utf-8")
    result = await ReadFileTool().execute(context, ReadFileArguments(path="large.txt"))
    assert result.status == "completed"
    assert result.truncated
    assert len(result.blob_references) == 1
    assert context.blobs.read(result.blob_references[0]) == b"x" * 2048

    escaped = await ReadFileTool().execute(context, ReadFileArguments(path="../outside.txt"))
    assert escaped.status == "failed"
    assert "relative" in escaped.summary


@pytest.mark.asyncio
async def test_home_paths_normalize_and_require_confirmation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_home = tmp_path / "user-home"
    user_home.mkdir()
    zshrc = user_home / ".zshrc"
    zshrc.write_text("export FIXTURE=1\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(user_home))

    relative = ReadFileArguments(path="~/.zshrc")
    absolute = ReadFileArguments(path=str(zshrc))
    assert relative.workspace == "home"
    assert relative.path == ".zshrc"
    assert absolute.workspace == "home"
    assert absolute.path == ".zshrc"

    context = tool_context(tmp_path)
    gate = PolicyGate(tmp_path / "hames-home")
    decision = gate.decide("read_file", relative, context)
    assert decision.decision is PolicyDecisionKind.REQUIRE_CONFIRMATION
    assert decision.risk == "outside_workspace"

    read = await ReadFileTool().execute(context, relative)
    assert read.status == "completed"
    assert read.content == "export FIXTURE=1\n"

    credential = user_home / ".ssh" / "config"
    credential.parent.mkdir()
    credential.write_text("Host fixture\n", encoding="utf-8")
    denied = gate.decide("read_file", ReadFileArguments(path="~/.ssh/config"), context)
    assert denied.decision is PolicyDecisionKind.DENY
    assert denied.risk == "secret"


@pytest.mark.asyncio
async def test_shell_captures_channels_filters_secrets_and_times_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = tool_context(tmp_path, shell_timeout_seconds=0.05, shell_max_timeout_seconds=1)
    completed = await ShellTool().execute(
        context,
        ShellArguments(command="printf out; printf err >&2", timeout_seconds=1),
    )
    assert completed.status == "completed"
    assert completed.structured_data["stdout"] == "out"
    assert completed.structured_data["stderr"] == "err"

    monkeypatch.setenv("FIXTURE_API_TOKEN", "must-not-cross-tool-boundary")
    filtered = await ShellTool().execute(
        context,
        ShellArguments(command='printf "%s" "${FIXTURE_API_TOKEN-unset}"', timeout_seconds=1),
    )
    assert filtered.structured_data["stdout"] == "unset"

    timed_out = await ShellTool().execute(context, ShellArguments(command="sleep 5"))
    assert timed_out.status == "failed"
    assert "timed out" in timed_out.summary


def test_policy_classifies_safe_dangerous_and_protected_actions(tmp_path: Path) -> None:
    context = tool_context(tmp_path)
    gate = PolicyGate(tmp_path / "hames-home")
    assert (
        gate.decide("shell", ShellArguments(command="cargo test"), context).decision
        is PolicyDecisionKind.ALLOW
    )
    assert (
        gate.decide("shell", ShellArguments(command="rm -rf target"), context).decision
        is PolicyDecisionKind.REQUIRE_CONFIRMATION
    )
    assert (
        gate.decide("shell", ShellArguments(command="cat ~/.hames/config.toml"), context).decision
        is PolicyDecisionKind.DENY
    )
    assert (
        gate.decide("shell", ShellArguments(command="cat /etc/passwd"), context).decision
        is PolicyDecisionKind.REQUIRE_CONFIRMATION
    )
    assert (
        gate.decide("shell", ShellArguments(command="cat ~/.zshrc"), context).decision
        is PolicyDecisionKind.REQUIRE_CONFIRMATION
    )


def test_execution_modes_are_gateway_policy_not_client_convention(tmp_path: Path) -> None:
    context = tool_context(tmp_path)
    gate = PolicyGate(tmp_path / "hames-home")
    write = WriteFileArguments(path="plan.txt", content="no")

    assert gate.decide("write_file", write, context).decision is PolicyDecisionKind.ALLOW
    manual = gate.decide("write_file", write, context, interaction_mode="manual")
    assert manual.decision is PolicyDecisionKind.REQUIRE_CONFIRMATION
    assert manual.risk == "manual_mode"
    assert (
        gate.decide(
            "write_file",
            write,
            context,
            interaction_mode="manual",
            session_tool_granted=True,
        ).decision
        is PolicyDecisionKind.ALLOW
    )
    assert (
        gate.decide("write_file", write, context, interaction_mode="plan").decision
        is PolicyDecisionKind.DENY
    )
    assert (
        gate.decide(
            "shell", ShellArguments(command="cargo test"), context, interaction_mode="plan"
        ).decision
        is PolicyDecisionKind.ALLOW
    )
    assert (
        gate.decide(
            "shell", ShellArguments(command="cargo fmt"), context, interaction_mode="plan"
        ).decision
        is PolicyDecisionKind.DENY
    )
    assert (
        gate.decide(
            "shell",
            ShellArguments(command="pytest && rm -rf src"),
            context,
            interaction_mode="plan",
        ).decision
        is PolicyDecisionKind.DENY
    )
    assert (
        gate.decide(
            "shell",
            ShellArguments(command="rm -rf target"),
            context,
            interaction_mode="manual",
            session_tool_granted=True,
        ).decision
        is PolicyDecisionKind.REQUIRE_CONFIRMATION
    )


def test_self_management_tools_are_typed_and_destructive_controls_confirm(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    assert {
        "memory_search",
        "memory_add",
        "memory_edit",
        "memory_forget",
        "scar_list",
        "scar_record",
        "scar_control",
        "skill_catalog",
        "skill_control",
    } <= registry.names()
    with pytest.raises(ValueError, match="replacement field"):
        registry.validate("memory_edit", {"memory_id": "memory-1"})

    context = tool_context(tmp_path)
    gate = PolicyGate(tmp_path / "hames-home")
    assert (
        gate.decide("memory_forget", MemoryForgetArguments(memory_id="memory-1"), context).decision
        is PolicyDecisionKind.REQUIRE_CONFIRMATION
    )
    assert (
        gate.decide(
            "scar_control",
            ScarControlArguments(scar_id="scar-1", action="dismiss", reason="obsolete"),
            context,
        ).decision
        is PolicyDecisionKind.REQUIRE_CONFIRMATION
    )
    assert (
        gate.decide(
            "skill_control",
            SkillControlArguments(id="tests", action="rollback", reason="regressed"),
            context,
        ).decision
        is PolicyDecisionKind.REQUIRE_CONFIRMATION
    )
    assert (
        gate.decide(
            "memory_edit",
            MemoryEditArguments(memory_id="memory-1", summary="Corrected preference"),
            context,
        ).decision
        is PolicyDecisionKind.ALLOW
    )


def test_trust_and_approvals_are_exact_durable_and_one_shot(tmp_path: Path) -> None:
    database = Database(tmp_path / "home" / "hames.db")
    database.migrate()
    controls = ControlStore(database)
    project = tmp_path / "project"
    project.mkdir()
    grant = controls.grant_trust(project)
    assert controls.get_trust(project) == grant

    # Approval rows reference real durable sessions.
    from hames.ledger import Ledger

    session = Ledger(database).create_session(
        working_directory=project,
        agent_id="default",
        provider="fake",
        model="fixture",
    )
    arguments: dict[str, JsonValue] = {"command": "rm -rf target"}
    request_hash = approval_request_hash(
        tool_name="shell",
        arguments=arguments,
        session_id=session.id,
        run_id="run-1",
        agent_id="default",
        working_directory=str(project.resolve()),
    )
    approval = controls.create_approval(
        session_id=session.id,
        run_id="run-1",
        agent_id="default",
        working_directory=str(project.resolve()),
        tool_call_id="call-1",
        tool_name="shell",
        arguments=arguments,
        request_hash=request_hash,
        reason="recursive forced deletion",
    )
    with pytest.raises(ValueError, match="hash"):
        controls.resolve_approval(approval.id, "0" * 64, "approved")
    assert controls.resolve_approval(approval.id, request_hash, "denied").status == "denied"
    with pytest.raises(RuntimeError, match="already"):
        controls.resolve_approval(approval.id, request_hash, "approved")

    session_request_hash = approval_request_hash(
        tool_name="write_file",
        arguments={"path": "notes.txt", "content": "fixture"},
        session_id=session.id,
        run_id="run-2",
        agent_id="default",
        working_directory=str(project.resolve()),
    )
    session_approval = controls.create_approval(
        session_id=session.id,
        run_id="run-2",
        agent_id="default",
        working_directory=str(project.resolve()),
        tool_call_id="call-2",
        tool_name="write_file",
        arguments={"path": "notes.txt", "content": "fixture"},
        request_hash=session_request_hash,
        reason="manual mode",
        allow_session=True,
    )
    resolved = controls.resolve_approval(
        session_approval.id, session_request_hash, "approved_session"
    )
    assert resolved.status == "approved"
    assert resolved.approval_scope == "session"
    assert controls.has_session_tool_grant(session.id, "write_file")
    assert controls.revoke_trust(project)
    assert controls.get_trust(project) is None
