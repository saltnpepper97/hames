from __future__ import annotations

from pathlib import Path

import pytest

from hames.agent import AgentRegistry, load_agent, permitted_tools
from hames.paths import HamesPaths


def test_registry_creates_valid_portable_capsules(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    coder = registry.create("Coder")
    assert coder.metadata.id == "coder"
    assert coder.metadata.name == "Coder"
    assert coder.metadata.authority == "standard"
    assert coder.path == hames_paths.agents / "coder" / "AGENT.md"
    assert coder.path.stat().st_mode & 0o777 == 0o600
    assert [item.id for item in registry.list()] == ["coder", "default"]


def test_create_slugs_name_and_allocates_unnamed_ids(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    first = registry.create()
    second = registry.create()
    assert first.metadata.id == "hames-1"
    assert first.metadata.name == "hames-1"
    assert second.metadata.id == "hames-2"
    reviewer = registry.create("Code Reviewer")
    assert reviewer.metadata.id == "code-reviewer"
    assert reviewer.metadata.name == "Code Reviewer"
    duplicate = registry.create("Code Reviewer")
    assert duplicate.metadata.id == "code-reviewer-2"
    assert duplicate.metadata.name == "Code Reviewer"


def test_create_from_source_honors_frontmatter_id(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    source = (
        "---\nid: reviewer\nname: Code Reviewer\nauthority: read_only\n"
        "tools:\n  deny: [write_file, edit_file]\n---\nReview the diff.\n"
    )
    capsule = registry.create(source=source)
    assert capsule.metadata.id == "reviewer"
    assert capsule.metadata.name == "Code Reviewer"
    assert capsule.metadata.authority == "read_only"
    assert capsule.metadata.tools.deny == ["write_file", "edit_file"]
    assert capsule.instructions == "Review the diff."
    with pytest.raises(FileExistsError):
        registry.create(source=source)


def test_capsule_is_strict_and_legacy_provider_fields_are_inert(tmp_path: Path) -> None:
    path = tmp_path / "AGENT.md"
    path.write_text(
        "---\nid: reviewer\nname: Reviewer\nprovider: inherit\nmodel: ''\n"
        "authority: read_only\ntools:\n  deny: [shell]\n---\nReview carefully.\n",
        encoding="utf-8",
    )
    capsule = load_agent(path)
    assert capsule.metadata.authority == "read_only"
    assert capsule.deprecated_fields == ["provider", "model"]

    path.write_text("---\nid: Bad Name\nname: Bad\n---\nNope\n", encoding="utf-8")
    with pytest.raises(ValueError, match="agent ID"):
        load_agent(path)


def test_retirement_preserves_capsule_outside_active_registry(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    registry.create("Reviewer", authority="read_only")
    retired = registry.retire("reviewer")
    assert (retired / "AGENT.md").is_file()
    assert [item.id for item in registry.list()] == ["default"]
    with pytest.raises(ValueError, match="default"):
        registry.retire("default")


def test_read_only_and_tool_lists_only_restrict_authority(tmp_path: Path) -> None:
    path = tmp_path / "AGENT.md"
    path.write_text(
        "---\nid: reviewer\nname: Reviewer\nauthority: read_only\ntools:\n"
        "  allow: [read_file, list_dir, write_file]\n---\nReview only.\n",
        encoding="utf-8",
    )
    capsule = load_agent(path)
    assert permitted_tools(capsule, {"read_file", "list_dir", "write_file", "shell"}) == {
        "read_file",
        "list_dir",
    }
