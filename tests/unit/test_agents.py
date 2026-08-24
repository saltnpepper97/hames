from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from hames.agent import (
    AgentRegistry,
    apply_agent_skill_policy,
    load_agent,
    permitted_tools,
    skill_permitted,
)
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


def test_plugin_tools_follow_capsule_authority(tmp_path: Path) -> None:
    available = {"read_file", "list_dir", "shell", "project-stats.summary"}
    read_only = tmp_path / "read-only.md"
    read_only.write_text(
        "---\nid: reviewer\nname: Reviewer\nauthority: read_only\n---\nReview.\n",
        encoding="utf-8",
    )
    assert permitted_tools(load_agent(read_only), available) == {"read_file", "list_dir"}
    denied = tmp_path / "denied.md"
    denied.write_text(
        "---\nid: coder\nname: Coder\ntools:\n  deny: [project-stats.summary]\n---\nCode.\n",
        encoding="utf-8",
    )
    assert permitted_tools(load_agent(denied), available) == {"read_file", "list_dir", "shell"}
    pinned = tmp_path / "pinned.md"
    pinned.write_text(
        "---\nid: stats\nname: Stats\ntools:\n  allow: [project-stats.summary]\n---\nStats.\n",
        encoding="utf-8",
    )
    assert permitted_tools(load_agent(pinned), available) == {"project-stats.summary"}


@dataclass
class _Skill:
    slug: str
    score: float = 0.0


def test_skill_policy_filters_and_pins(tmp_path: Path) -> None:
    path = tmp_path / "AGENT.md"
    path.write_text(
        "---\nid: reviewer\nname: Reviewer\nskills:\n"
        "  deny: [deployment]\n  pin: [testing]\n---\nReview only.\n",
        encoding="utf-8",
    )
    capsule = load_agent(path)
    assert skill_permitted(capsule, "testing")
    assert not skill_permitted(capsule, "deployment")
    catalog = apply_agent_skill_policy(
        capsule,
        [
            _Skill("deployment", 1.0),
            _Skill("rust-development", 0.9),
            _Skill("testing", 0.1),
        ],
        limit=8,
    )
    assert [item.slug for item in catalog] == ["testing", "rust-development"]


def test_skill_allow_list_is_a_reduction(tmp_path: Path) -> None:
    path = tmp_path / "AGENT.md"
    path.write_text(
        "---\nid: rust\nname: Rust\nskills:\n"
        "  allow: [rust-development, testing]\n  pin: [testing]\n---\nStay on Rust.\n",
        encoding="utf-8",
    )
    capsule = load_agent(path)
    catalog = apply_agent_skill_policy(
        capsule,
        [
            _Skill("cmake-cpp", 1.0),
            _Skill("rust-development", 0.2),
            _Skill("testing", 0.1),
        ],
        limit=8,
    )
    assert [item.slug for item in catalog] == ["testing", "rust-development"]
    path.write_text(
        "---\nid: rust\nname: Rust\nskills:\n  allow: [rust]\n  pin: [testing]\n---\nNope.\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="pin list must be a subset"):
        load_agent(path)
