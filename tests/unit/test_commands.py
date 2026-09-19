from pathlib import Path

import pytest

from hames.commands import load_commands


def write_command(path: Path, agent: str = "coordinator") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'description = "Review the approved plan"\naction = "execute_plan"\nagent = "{agent}"\n'
    )


def test_commands_are_user_owned_and_workspace_overrides_global(tmp_path: Path) -> None:
    root, workspace = tmp_path / "home", tmp_path / "repo"
    assert load_commands(root, workspace) == []
    write_command(root / "commands" / "review.toml")
    write_command(workspace / ".hames" / "commands" / "review.toml", "local-reviewer")
    commands = load_commands(root, workspace)
    assert len(commands) == 1
    assert commands[0].name == "review"
    assert commands[0].agent == "local-reviewer"
    assert commands[0].source.endswith("repo/.hames/commands/review.toml")


@pytest.mark.parametrize("name", ["plan", "model", "help", "Bad_Name"])
def test_commands_cannot_override_builtins(tmp_path: Path, name: str) -> None:
    write_command(tmp_path / "commands" / f"{name}.toml")
    with pytest.raises(ValueError, match="invalid or reserved"):
        load_commands(tmp_path, tmp_path / "repo")


def test_invalid_override_does_not_silently_run_global_command(tmp_path: Path) -> None:
    write_command(tmp_path / "commands/review.toml")
    bad = tmp_path / "repo/.hames/commands/review.toml"
    bad.parent.mkdir(parents=True)
    bad.write_text('agent = "reviewer"\naction = "shell"\ndescription = "Invalid"\n')
    with pytest.raises(ValueError):
        load_commands(tmp_path, tmp_path / "repo")
