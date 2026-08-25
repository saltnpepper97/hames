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
    AskUserArguments,
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
    TaskUpdateArguments,
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
        WriteFileArguments(path="src/value.txt", content="alpha\nbeta\n"),
    )
    assert written.status == "completed"
    assert written.structured_data["created"] is True
    assert "--- /dev/null" in written.content
    assert "+++ b/src/value.txt" in written.content
    assert "+alpha" in written.content

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


def test_write_file_ignores_unknown_model_fields() -> None:
    arguments = ToolRegistry().validate(
        "write_file",
        {
            "path": "src/game.py",
            "content": "print(1)\n",
            "explanation": "create the game entrypoint",
        },
    )
    assert isinstance(arguments, WriteFileArguments)
    assert arguments.path == "src/game.py"
    assert arguments.create_parents is True


def test_ask_user_schema_limits_and_normalizes_choices() -> None:
    arguments = ToolRegistry().validate(
        "ask_user",
        {"question": " Which direction? ", "options": [" Keep it ", "Replace it"]},
    )
    assert isinstance(arguments, AskUserArguments)
    assert arguments.question == "Which direction?"
    assert [option.label for option in arguments.options] == ["Keep it", "Replace it"]
    assert [option.description for option in arguments.options] == ["", ""]
    described = ToolRegistry().validate(
        "ask_user",
        {
            "question": "Choose an approach",
            "options": [
                {
                    "label": "Careful",
                    "description": "Verify the current behavior.\n\nThen change it incrementally.",
                }
            ],
        },
    )
    assert isinstance(described, AskUserArguments)
    assert described.options[0].description == (
        "Verify the current behavior.\n\nThen change it incrementally."
    )
    with pytest.raises(ValueError, match="at most 3 items"):
        ToolRegistry().validate(
            "ask_user",
            {"question": "Choose", "options": ["one", "two", "three", "four"]},
        )
    with pytest.raises(ValueError, match="unique"):
        ToolRegistry().validate("ask_user", {"question": "Choose", "options": ["Same", "same"]})
    with pytest.raises(ValueError, match="question must not be empty"):
        ToolRegistry().validate("ask_user", {"question": "   "})


@pytest.mark.asyncio
async def test_home_paths_normalize_and_auto_allows_non_secret_access(
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
    assert decision.decision is PolicyDecisionKind.ALLOW
    manual = gate.decide("read_file", relative, context, interaction_mode="manual")
    assert manual.decision is PolicyDecisionKind.REQUIRE_CONFIRMATION
    assert manual.risk == "outside_workspace"
    home_write = WriteFileArguments(path="~/notes.txt", content="ordinary")
    assert gate.decide("write_file", home_write, context).decision is PolicyDecisionKind.ALLOW
    assert (
        gate.decide("write_file", home_write, context, interaction_mode="manual").decision
        is PolicyDecisionKind.REQUIRE_CONFIRMATION
    )

    read = await ReadFileTool().execute(context, relative)
    assert read.status == "completed"
    assert read.content == "export FIXTURE=1\n"

    credential = user_home / ".ssh" / "config"
    credential.parent.mkdir()
    credential.write_text("Host fixture\n", encoding="utf-8")
    denied = gate.decide("read_file", ReadFileArguments(path="~/.ssh/config"), context)
    assert denied.decision is PolicyDecisionKind.DENY
    assert denied.risk == "secret"

    codex_memory = user_home / ".codex" / "memories" / "MEMORY.md"
    codex_memory.parent.mkdir(parents=True)
    codex_memory.write_text("provider-private\n", encoding="utf-8")
    provider_state = gate.decide(
        "read_file", ReadFileArguments(path="~/.codex/memories/MEMORY.md"), context
    )
    assert provider_state.decision is PolicyDecisionKind.DENY
    assert provider_state.risk == "protected_state"
    assert (
        gate.decide(
            "shell", ShellArguments(command="cat ~/.codex/memories/MEMORY.md"), context
        ).decision
        is PolicyDecisionKind.DENY
    )


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
        is PolicyDecisionKind.ALLOW
    )
    assert (
        gate.decide("shell", ShellArguments(command="cat ~/.zshrc"), context).decision
        is PolicyDecisionKind.ALLOW
    )
    assert (
        gate.decide(
            "shell",
            ShellArguments(command="cat ~/.zshrc"),
            context,
            interaction_mode="manual",
        ).decision
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
    task_update = TaskUpdateArguments(action="add", text="Discovered work")
    assert (
        gate.decide("task_update", task_update, context, interaction_mode="plan").decision
        is PolicyDecisionKind.DENY
    )
    assert (
        gate.decide("task_update", task_update, context, interaction_mode="manual").decision
        is PolicyDecisionKind.REQUIRE_CONFIRMATION
    )
    assert (
        gate.decide("task_update", task_update, context, interaction_mode="auto").decision
        is PolicyDecisionKind.ALLOW
    )
    assert (
        gate.decide(
            "shell", ShellArguments(command="cargo test"), context, interaction_mode="plan"
        ).decision
        is PolicyDecisionKind.ALLOW
    )
    probe = (
        'python3 --version && python3 -c "import curses, pty, json; '
        "print('curses ok, pty ok', curses.version)\""
    )
    assert (
        gate.decide(
            "shell", ShellArguments(command=probe), context, interaction_mode="plan"
        ).decision
        is PolicyDecisionKind.ALLOW
    )
    availability_probe = (
        'python3 --version && python3 -c "import pygame; '
        "print('pygame', pygame.version.ver)\" 2>&1 | tail -1"
    )
    assert (
        gate.decide(
            "shell", ShellArguments(command=availability_probe), context, interaction_mode="plan"
        ).decision
        is PolicyDecisionKind.ALLOW
    )
    assert (
        gate.decide(
            "shell",
            ShellArguments(
                command='command -v python3 && python3 -c "import pygame"; echo "exit=$?"'
            ),
            context,
            interaction_mode="plan",
        ).decision
        is PolicyDecisionKind.ALLOW
    )
    safe_home_inspection = (
        'echo "HOME=$HOME"; echo "---"; ls -la ~/ | head -40; '
        'echo "---snake?---"; ls -la ~/snake.py ~/snake*.py 2>/dev/null; echo "done"'
    )
    assert (
        gate.decide(
            "shell",
            ShellArguments(command=safe_home_inspection),
            context,
            interaction_mode="plan",
        ).decision
        is PolicyDecisionKind.ALLOW
    )
    for safe_python_probe in (
        "python3 -c \"import os; print(os.environ.get('DISPLAY')); "
        "print(os.environ.get('WAYLAND_DISPLAY'))\"",
        'SDL_VIDEODRIVER=dummy python3 -c "import pygame; pygame.init(); '
        "pygame.display.set_mode((320,240)); print('headless OK')\"",
        'python3 -c "def next_cell(c, d): return ((c[0]+d[0])%10, (c[1]+d[1])%10); '
        "assert next_cell((0,0),(1,0))==(1,0); print('logic OK')\"",
        "python3 -c \"import numpy; print('numpy', numpy.__version__)\"",
        "python3 -c \"import numpy; print('numpy', numpy.__version__)\" 2>&1; "
        'echo "---"; ls ~ | head -30',
        "python3 -m pip show numpy | head -3",
        'SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 -c "\n'
        "import pygame\npygame.init()\ns = pygame.display.set_mode((320,240))\n"
        "print('dummy display OK', s.get_size())\npygame.quit()\n"
        '" 2>&1 | grep -v avx2',
        "SDL_VIDEODRIVER=dummy SDL_AUDIODRIVER=dummy python3 - <<'EOF' "
        "2>&1 | grep -vi avx2\nimport pygame\nok = pygame.display.init()\n"
        'print("display init:", ok)\ns = pygame.display.set_mode((320, 240))\n'
        'print("surface:", s.get_size())\npygame.quit()\nEOF',
        "env | grep -E '^(DISPLAY|WAYLAND_DISPLAY|XDG_SESSION_TYPE)='; "
        "ls /tmp/.X11-unix 2>/dev/null; true",
        'echo "DISPLAY=$DISPLAY WAYLAND_DISPLAY=$WAYLAND_DISPLAY '
        'XDG_SESSION_TYPE=$XDG_SESSION_TYPE"; python3 -c "\nimport os\n'
        "os.environ.setdefault('SDL_VIDEODRIVER','dummy')\nimport pygame\npygame.init()\n"
        "s = pygame.display.set_mode((320,240))\n"
        "print('dummy-driver display OK:', s.get_size())\npygame.quit()\n"
        '" 2>&1 | grep -v RuntimeWarning | grep -v avx2',
    ):
        assert (
            gate.decide(
                "shell",
                ShellArguments(command=safe_python_probe),
                context,
                interaction_mode="plan",
            ).decision
            is PolicyDecisionKind.ALLOW
        )
    for navigation_probe in (
        "cd /tmp && pwd && ls -la",
        "cd ~/ && test -d . && stat .",
    ):
        assert (
            gate.decide(
                "shell",
                ShellArguments(command=navigation_probe),
                context,
                interaction_mode="plan",
            ).decision
            is PolicyDecisionKind.ALLOW
        )
    assert (
        gate.decide(
            "shell",
            ShellArguments(command="python3 -c \"open('plan-write.txt', 'w').write('bad')\""),
            context,
            interaction_mode="plan",
        ).decision
        is PolicyDecisionKind.DENY
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


def test_blocking_a_task_requires_a_reason() -> None:
    with pytest.raises(ValueError, match="requires blocked_reason"):
        TaskUpdateArguments(action="update", task_id="task-1", status="blocked")
    blocked = TaskUpdateArguments(
        action="update",
        task_id="task-1",
        status="blocked",
        blocked_reason="dependency lookup failed",
    )
    assert blocked.blocked_reason == "dependency lookup failed"


def test_self_management_tools_are_typed_and_destructive_controls_confirm(
    tmp_path: Path,
) -> None:
    registry = ToolRegistry()
    assert {
        "ask_user",
        "memory_search",
        "memory_add",
        "memory_edit",
        "memory_forget",
        "scar_list",
        "scar_record",
        "scar_control",
        "skill_catalog",
        "skill_control",
        "session_title_set",
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
            "memory_forget",
            MemoryForgetArguments(memory_id="memory-1"),
            context,
            interaction_mode="auto",
            user_requested_memory_maintenance=True,
        ).decision
        is PolicyDecisionKind.ALLOW
    )
    assert (
        gate.decide(
            "memory_forget",
            MemoryForgetArguments(memory_id="memory-1"),
            context,
            interaction_mode="manual",
            user_requested_memory_maintenance=True,
        ).decision
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
            "scar_control",
            ScarControlArguments(scar_id="scar-1", action="delete", reason="recorded in error"),
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
