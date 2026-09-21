from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pytest

from hames.agent import (
    AgentAvatar,
    AgentRegistry,
    AgentSkills,
    AgentTools,
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
    assert UUID(coder.metadata.id.removeprefix("agent-")).version == 4
    assert coder.metadata.slug == ""
    assert registry.load("Coder").metadata.id == coder.metadata.id
    assert coder.metadata.name == "Coder"
    assert coder.metadata.authority == "standard"
    assert coder.path == hames_paths.agents / coder.metadata.id / "AGENT.md"
    assert coder.path.stat().st_mode & 0o777 == 0o600
    assert {item.id for item in registry.list()} == {coder.metadata.id, "default"}


def test_create_allocates_opaque_ids_independently_of_names(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    first = registry.create()
    second = registry.create()
    assert first.metadata.name == "hames-1"
    assert first.metadata.name == "hames-1"
    assert second.metadata.name == "hames-2"
    reviewer = registry.create("Code Reviewer")
    assert reviewer.metadata.id.startswith("agent-")
    assert reviewer.metadata.name == "Code Reviewer"
    duplicate = registry.create("Code Reviewer")
    assert duplicate.metadata.id != reviewer.metadata.id
    with pytest.raises(ValueError, match="Ambiguous"):
        registry.load("Code Reviewer")
    assert registry.reference(reviewer.metadata.id) == reviewer.metadata.id
    assert duplicate.metadata.name == "Code Reviewer"


def test_create_from_source_honors_frontmatter_id(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    source = (
        "---\nid: reviewer\nslug: careful-reviewer\nname: Code Reviewer\nauthority: read_only\n"
        "tools:\n  deny: [write_file, edit_file]\n---\nReview the diff.\n"
    )
    capsule = registry.create(source=source)
    assert capsule.metadata.id == "reviewer"
    assert registry.load("careful-reviewer").metadata.id == "reviewer"
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


def test_default_agent_can_be_customized_but_keeps_stable_identity(
    hames_paths: HamesPaths,
) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)

    updated = registry.update(
        "default",
        name="Navigator",
        instructions="# Role\nGuide the work carefully.",
    )

    assert updated.metadata.id == "default"
    assert updated.metadata.name == "Navigator"
    assert updated.instructions == "# Role\nGuide the work carefully."
    assert updated.path == hames_paths.default_agent
    assert updated.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="default"):
        registry.retire("default")


def test_agent_update_changes_access_without_losing_other_metadata(
    hames_paths: HamesPaths,
) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    original = registry.load("default")

    updated = registry.update(
        "default",
        tools=AgentTools(deny=["shell"]),
        skills=AgentSkills(deny=["deployment"], pin=["testing"]),
    )

    assert updated.metadata.id == "default"
    assert updated.metadata.name == original.metadata.name
    assert updated.instructions == original.instructions
    assert updated.metadata.tools.deny == ["shell"]
    assert updated.metadata.skills.deny == ["deployment"]
    assert updated.metadata.skills.pin == ["testing"]


def test_agent_avatar_is_portable_and_updates_without_losing_metadata(
    hames_paths: HamesPaths,
) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    original = registry.load("default")

    updated = registry.update(
        "default",
        avatar=AgentAvatar(shape="hex", eyes="pill", color="#A855F7"),
    )

    assert updated.metadata.name == original.metadata.name
    assert updated.instructions == original.instructions
    assert updated.metadata.avatar == AgentAvatar(shape="hex", eyes="pill", color="#a855f7")
    assert registry.list()[0].avatar == updated.metadata.avatar
    assert "avatar:" in updated.path.read_text(encoding="utf-8")


def test_agent_avatar_rejects_invalid_color() -> None:
    with pytest.raises(ValueError, match="six-digit hex"):
        AgentAvatar(color="purple")


def test_agent_avatar_migrates_early_shape_names() -> None:
    assert AgentAvatar.model_validate({"shape": "round"}).shape == "circle"
    assert AgentAvatar.model_validate({"shape": "arch"}).shape == "triangle"
    assert AgentAvatar.model_validate({"shape": "capsule"}).shape == "cloud"
    assert AgentAvatar.model_validate({"eyes": "happy"}).eyes == "pill"
    assert AgentAvatar.model_validate({"face": "outline"}).face == "solid"


def test_agent_update_rejects_identity_change_without_touching_capsule(
    hames_paths: HamesPaths,
) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    original = hames_paths.default_agent.read_text(encoding="utf-8")

    with pytest.raises(ValueError, match="ID cannot be changed"):
        registry.update(
            "default",
            source="---\nid: replacement\nname: Replacement\n---\nTake over.\n",
        )

    assert hames_paths.default_agent.read_text(encoding="utf-8") == original


def test_read_only_and_tool_lists_only_restrict_authority(tmp_path: Path) -> None:
    path = tmp_path / "AGENT.md"
    path.write_text(
        "---\nid: reviewer\nname: Reviewer\nauthority: read_only\ntools:\n"
        "  allow: [read_file, list_dir, write_file]\n---\nReview only.\n",
        encoding="utf-8",
    )
    capsule = load_agent(path)
    assert permitted_tools(
        capsule, {"ask_user", "read_file", "list_dir", "write_file", "shell"}
    ) == {
        "ask_user",
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


def test_rename_preserves_opaque_identity_and_old_references(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    original = registry.create("Builder")
    renamed = registry.update(original.metadata.id, name="Careful Reviewer")
    assert renamed.metadata.id == original.metadata.id
    assert renamed.path == original.path
    assert renamed.metadata.slug == ""
    assert registry.load("Careful Reviewer").metadata.id == original.metadata.id
    assert registry.load("builder").metadata.name == "Careful Reviewer"
    again = registry.update("Careful Reviewer", name="Final Reviewer")
    assert again.metadata.slug == ""
    assert registry.load("Careful Reviewer").metadata.id == original.metadata.id
    assert registry.load("final-reviewer").metadata.id == original.metadata.id
    assert registry.reference(original.metadata.id) == "Final Reviewer"
    with pytest.raises(ValueError, match="historical alias"):
        registry.create("Careful Reviewer")


def test_duplicate_names_never_retarget_an_id(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    first = registry.create("Builder")
    second = registry.create("Reviewer")
    registry.update(first.metadata.id, name="Reviewer")
    assert registry.load(first.metadata.id).metadata.id == first.metadata.id
    assert registry.load(second.metadata.id).metadata.id == second.metadata.id
    with pytest.raises(ValueError, match="Ambiguous"):
        registry.load("Reviewer")


def test_delegation_references_are_saved_as_stable_ids(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    registry.create(source="---\nid: worker-id\nname: Builder\n---\nWork.\n")
    registry.update("worker-id", name="Edward")
    coordinator = registry.create(
        source=(
            "---\nid: coordinator\nname: Coordinator\ndelegation:\n"
            "  allowed_agents: [edward]\n---\nDelegate.\n"
        )
    )
    assert coordinator.metadata.delegation.allowed_agents == ["worker-id"]
    source = coordinator.path.read_text().replace("worker-id", "edward")
    updated = registry.update("coordinator", source=source)
    assert updated.metadata.delegation.allowed_agents == ["worker-id"]
    renamed = registry.update("worker-id", name="New Name")
    # Replacing source cannot erase aliases that an in-flight request may still use.
    registry.update(
        "worker-id", source=("---\nid: worker-id\nname: New Name\nslug: new-name\n---\nWork.\n")
    )
    assert registry.load("edward").metadata.id == "worker-id"
    assert renamed.path == registry.load("worker-id").path
    with pytest.raises(ValueError, match="alias"):
        registry.create(source="---\nid: impostor\nname: Impostor\naliases: [edward]\n---\nWork.")


def test_unicode_names_resolve_and_remain_aliases_after_rename(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    registry = AgentRegistry(hames_paths.agents)
    worker = registry.create("研究員")
    assert registry.reference(worker.metadata.id) == "研究員"
    registry.update(worker.metadata.id, name="Research")
    assert registry.load("研究員").metadata.id == worker.metadata.id
    other = registry.create("Other")
    with pytest.raises(ValueError, match="historical alias"):
        registry.update(other.metadata.id, name="研究員")
