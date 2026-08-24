"""Deterministic M03 policy gate for core tools."""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from hames.providers.base import JsonValue
from hames.rules import PolicyRule
from hames.tools import (
    MemoryForgetArguments,
    ScarControlArguments,
    ShellArguments,
    SkillControlArguments,
    ToolArguments,
    ToolContext,
    WorkspaceArguments,
)


class PolicyDecisionKind(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    decision: PolicyDecisionKind
    reason: str
    risk: str = "ordinary"


_DENIED_SHELL = (
    (
        re.compile(r"(^|[;&|]\s*)\s*(mkfs(?:\.[a-z0-9]+)?|fdisk|parted)\b", re.I),
        "raw filesystem mutation",
    ),
    (re.compile(r"\bdd\b[^\n]*(?:of=)?/dev/(?:sd|nvme|vd|hd)", re.I), "raw disk write"),
    (re.compile(r"(?:^|[\s'\"])(?:~?/)?\.hames(?:[/\s'\"]|$)"), "Hames state access"),
    (
        re.compile(r"(?:^|[\s'\"])(?:~?/)?\.(?:ssh|gnupg|aws)(?:[/\s'\"]|$)"),
        "credential store access",
    ),
    (
        re.compile(r"(?:^|[\s'\"])(?:~?/)?\.(?:kube|env)(?:[/\s'\"]|$)"),
        "secret configuration access",
    ),
)

_CONFIRM_SHELL = (
    (
        re.compile(r"\brm\b[^\n;&|]*\s(?:-[A-Za-z]*r[A-Za-z]*|--recursive)(?:\s|$)", re.I),
        "recursive deletion",
    ),
    (
        re.compile(r"\bgit\s+(?:reset\s+--hard|clean\s+-[^\s]*f|push\b[^\n]*--force)", re.I),
        "destructive Git operation",
    ),
    (re.compile(r"(^|[;&|]\s*)\s*(?:sudo|su)\b", re.I), "privilege escalation"),
    (
        re.compile(r"(^|[;&|]\s*)\s*(?:shutdown|reboot|poweroff|halt)\b", re.I),
        "host power operation",
    ),
    (re.compile(r"\b(?:killall|pkill)\b", re.I), "broad process termination"),
    (re.compile(r"\bchmod\s+(?:-[^\s]+\s+)*777\b", re.I), "broad permission change"),
)

_SECRET_NAMES = {
    ".env",
    "id_rsa",
    "id_ed25519",
    "credentials",
    "credentials.json",
}

_SECRET_DIRECTORIES = {".ssh", ".gnupg", ".aws", ".kube"}

_OUTSIDE_PATH = re.compile(r"(?<![\w$])/(?:home|root|etc|var|opt|srv|mnt|media)/[^\s'\";&|]+")

_PLAN_DENIED_TOOLS = {
    "write_file",
    "edit_file",
    "spawn_agent",
    "skill_author",
    "skill_control",
    "memory_add",
    "memory_edit",
    "memory_forget",
    "scar_record",
    "scar_control",
    "task_update",
}

_MANUAL_CONFIRM_TOOLS = {
    "write_file",
    "edit_file",
    "shell",
    "skill_author",
    "skill_control",
    "memory_add",
    "memory_edit",
    "memory_forget",
    "scar_record",
    "scar_control",
    "task_update",
}


class PolicyGate:
    def __init__(self, protected_root: Path) -> None:
        self.protected_root = protected_root.expanduser().resolve(strict=False)

    def decide(
        self,
        tool_name: str,
        arguments: ToolArguments,
        context: ToolContext,
        *,
        allowed_tools: frozenset[str] | None = None,
        declarative_rules: Sequence[PolicyRule] = (),
        interaction_mode: str = "auto",
        session_tool_granted: bool = False,
        user_requested_memory_maintenance: bool = False,
    ) -> PolicyDecision:
        if allowed_tools is not None and tool_name not in allowed_tools:
            return PolicyDecision(
                PolicyDecisionKind.DENY,
                "the active agent is not allowed to use this tool",
                "agent_scope",
            )
        if interaction_mode == "plan" and (tool_name in _PLAN_DENIED_TOOLS or "." in tool_name):
            return PolicyDecision(
                PolicyDecisionKind.DENY,
                "plan mode does not permit code or durable state changes",
                "plan_mode",
            )
        if (
            interaction_mode == "plan"
            and isinstance(arguments, ShellArguments)
            and not _plan_shell_allowed(arguments.command)
        ):
            return PolicyDecision(
                PolicyDecisionKind.DENY,
                "plan mode permits only inspection and test commands",
                "plan_mode",
            )
        if isinstance(arguments, MemoryForgetArguments):
            if interaction_mode == "auto" and user_requested_memory_maintenance:
                return PolicyDecision(
                    PolicyDecisionKind.ALLOW,
                    "the user explicitly requested memory maintenance in auto mode",
                    "personal_state",
                )
            return PolicyDecision(
                PolicyDecisionKind.REQUIRE_CONFIRMATION,
                "forgetting permanently deletes a durable memory",
                "personal_state",
            )
        if isinstance(arguments, ScarControlArguments) and arguments.action in {
            "dismiss",
            "delete",
        }:
            return PolicyDecision(
                PolicyDecisionKind.REQUIRE_CONFIRMATION,
                "deleting permanently removes a behavioral scar"
                if arguments.action == "delete"
                else "dismissing disables a behavioral scar",
                "personal_state",
            )
        if isinstance(arguments, SkillControlArguments) and arguments.action in {
            "archive",
            "rollback",
        }:
            return PolicyDecision(
                PolicyDecisionKind.REQUIRE_CONFIRMATION,
                f"{arguments.action} changes the active Skill lifecycle",
                "personal_state",
            )
        rule_denial: PolicyDecision | None = None
        rule_confirmation: PolicyDecision | None = None
        if isinstance(arguments, ShellArguments) and declarative_rules:
            for rule in declarative_rules:
                if rule.status != "active" or rule.scope != "shell_command":
                    continue
                if re.search(rule.pattern, arguments.command) is None:
                    continue
                decision = (
                    PolicyDecision(PolicyDecisionKind.DENY, rule.reason, "declarative_rule")
                    if rule.action == "deny"
                    else PolicyDecision(
                        PolicyDecisionKind.REQUIRE_CONFIRMATION,
                        rule.reason,
                        "declarative_rule",
                    )
                )
                if rule.action == "deny" and rule_denial is None:
                    rule_denial = decision
                if rule.action == "confirm" and rule_confirmation is None:
                    rule_confirmation = decision
        if isinstance(arguments, WorkspaceArguments):
            path_value = getattr(arguments, "path", None)
            if isinstance(path_value, str):
                try:
                    target = context.resolve(
                        arguments.workspace,
                        path_value,
                        must_exist=tool_name in {"read_file", "list_dir", "edit_file"},
                    )
                except (OSError, ValueError) as exc:
                    return PolicyDecision(PolicyDecisionKind.DENY, str(exc), "path_escape")
                if target == self.protected_root or target.is_relative_to(self.protected_root):
                    return PolicyDecision(
                        PolicyDecisionKind.DENY,
                        "generic tools cannot access Hames configuration or state",
                        "protected_state",
                    )
                if target.name in _SECRET_NAMES or (
                    target.name.startswith(".env.") and target.name != ".env.example"
                ):
                    return PolicyDecision(
                        PolicyDecisionKind.DENY,
                        "known secret files are not available to generic tools",
                        "secret",
                    )
                if any(part in _SECRET_DIRECTORIES for part in target.parts):
                    return PolicyDecision(
                        PolicyDecisionKind.DENY,
                        "credential stores are not available to generic tools",
                        "secret",
                    )
                if arguments.workspace == "home" and interaction_mode != "auto":
                    return PolicyDecision(
                        PolicyDecisionKind.REQUIRE_CONFIRMATION,
                        "tool accesses the user home outside the trusted workspace",
                        "outside_workspace",
                    )

        if isinstance(arguments, ShellArguments):
            for pattern, reason in _DENIED_SHELL:
                if pattern.search(arguments.command):
                    return PolicyDecision(PolicyDecisionKind.DENY, reason, "prohibited")
            for pattern, reason in _CONFIRM_SHELL:
                if pattern.search(arguments.command):
                    return PolicyDecision(PolicyDecisionKind.REQUIRE_CONFIRMATION, reason, "high")
            if interaction_mode != "auto":
                workspace_root = context.root_for(arguments.workspace).resolve(strict=True)
                for raw_path in _OUTSIDE_PATH.findall(arguments.command):
                    candidate = Path(raw_path).expanduser().resolve(strict=False)
                    if candidate == workspace_root or candidate.is_relative_to(workspace_root):
                        continue
                    return PolicyDecision(
                        PolicyDecisionKind.REQUIRE_CONFIRMATION,
                        "command references a path outside the trusted workspace",
                        "outside_workspace",
                    )
                if re.search(r"(?:^|[\s'\"])(?:\.\./)+", arguments.command):
                    return PolicyDecision(
                        PolicyDecisionKind.REQUIRE_CONFIRMATION,
                        "command contains parent-directory traversal",
                        "outside_workspace",
                    )
                if re.search(r"(?:^|[\s'\"])~/", arguments.command):
                    return PolicyDecision(
                        PolicyDecisionKind.REQUIRE_CONFIRMATION,
                        "command references the user home outside the trusted workspace",
                        "outside_workspace",
                    )

        if rule_denial is not None:
            return rule_denial
        if rule_confirmation is not None:
            return rule_confirmation
        if (
            interaction_mode == "manual"
            and tool_name in _MANUAL_CONFIRM_TOOLS
            and not session_tool_granted
        ):
            return PolicyDecision(
                PolicyDecisionKind.REQUIRE_CONFIRMATION,
                "manual mode requires approval for state-changing tools",
                "manual_mode",
            )
        return PolicyDecision(PolicyDecisionKind.ALLOW, "allowed by trusted-root policy")


def _plan_shell_allowed(command: str) -> bool:
    if re.search(r"(?:>>?|<)|`|\$\(", command):
        return False
    clauses = [part.strip() for part in re.split(r"(?:&&|\|\||[;\n|])", command)]
    if not clauses or any(not clause for clause in clauses):
        return False
    return all(_plan_clause_allowed(clause) for clause in clauses)


def _plan_clause_allowed(clause: str) -> bool:
    try:
        words = shlex.split(clause)
    except ValueError:
        return False
    while words and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[0]):
        words.pop(0)
    if not words:
        return False
    command = words[0].removeprefix("./")
    args = words[1:]
    if command in {"pwd", "ls", "rg", "grep", "find", "head", "tail", "wc"}:
        return not any(arg in {"--delete", "-delete"} for arg in args)
    if command == "sed":
        return "-i" not in args and not any(arg.startswith("--in-place") for arg in args)
    if command == "git":
        return bool(args) and args[0] in {"status", "diff", "log", "show", "grep"}
    if command == "cargo":
        if not args or args[0] not in {"test", "check", "clippy", "fmt"}:
            return False
        return args[0] != "fmt" or "--check" in args
    if (
        command in {"pytest", "pyright"}
        or command.endswith("/pytest")
        or command.endswith("/pyright")
    ):
        return True
    if command == "ruff" or command.endswith("/ruff"):
        return "--fix" not in args and (not args or args[0] != "format" or "--check" in args)
    if command == "uv" and len(args) >= 2 and args[0] == "run":
        return _plan_clause_allowed(shlex.join(args[1:]))
    return False


def approval_request_hash(
    *,
    tool_name: str,
    arguments: dict[str, JsonValue],
    session_id: str,
    run_id: str,
    agent_id: str,
    working_directory: str,
) -> str:
    encoded = json.dumps(
        {
            "agent_id": agent_id,
            "arguments": arguments,
            "run_id": run_id,
            "session_id": session_id,
            "tool_name": tool_name,
            "working_directory": working_directory,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()
