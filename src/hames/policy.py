"""Deterministic M03 policy gate for core tools."""

from __future__ import annotations

import ast
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
        re.compile(r"(?:^|[\s'\"])(?:~?/)?\.codex(?:[/\s'\"]|$)"),
        "provider-private Codex state access",
    ),
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
    def __init__(
        self, protected_root: Path, *, provider_private_roots: Sequence[Path] | None = None
    ) -> None:
        self.protected_root = protected_root.expanduser().resolve(strict=False)
        self.provider_private_roots = tuple(
            root.expanduser().resolve(strict=False)
            for root in (
                provider_private_roots
                if provider_private_roots is not None
                else (Path.home() / ".codex",)
            )
        )

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
                if any(
                    target == root or target.is_relative_to(root)
                    for root in self.provider_private_roots
                ):
                    return PolicyDecision(
                        PolicyDecisionKind.DENY,
                        "generic tools cannot access provider-private Codex state",
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
                if arguments.workspace == "home" and interaction_mode == "manual":
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
            if interaction_mode == "manual":
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
    heredoc_command = _normalize_plan_python_heredoc(command)
    if heredoc_command is not None:
        return _plan_shell_allowed(heredoc_command)
    command = re.sub(
        r"\s+(?:[012]?>\s*/dev/null|[012]?>&[012])(?=\s*(?:$|&&|\|\||[;|]))",
        "",
        command,
    )
    if re.search(r"(?:>>?|<)|`|\$\(", command):
        return False
    clauses = _plan_shell_clauses(command)
    if not clauses or any(not clause for clause in clauses):
        return False
    return all(_plan_clause_allowed(clause) for clause in clauses)


def _normalize_plan_python_heredoc(command: str) -> str | None:
    """Turn a strict Python stdin probe into the equivalent validated ``-c`` probe."""
    match = re.fullmatch(
        r"(?s)(?P<launch>(?:(?:[A-Za-z_][A-Za-z0-9_]*=\S+)\s+)*"
        r"(?:\S*/)?python(?:3)?)\s+-\s+"
        r"<<(?P<quote>['\"]?)(?P<delimiter>[A-Za-z_][A-Za-z0-9_]*)(?P=quote)"
        r"(?P<suffix>[^\n]*)\n(?P<source>.*?)\n(?P=delimiter)\s*",
        command,
    )
    if match is None:
        return None
    source = match.group("source")
    return f"{match.group('launch')} -c {shlex.quote(source)}{match.group('suffix')}"


def _plan_shell_clauses(command: str) -> list[str]:
    """Split shell clauses without treating punctuation inside quotes as control syntax."""
    clauses: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    index = 0
    while index < len(command):
        character = command[index]
        if escaped:
            escaped = False
            index += 1
            continue
        if character == "\\" and quote != "'":
            escaped = True
            index += 1
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            index += 1
            continue
        if quote is None and character in {";", "\n", "|", "&"}:
            separator_length = 1
            if character in {"|", "&"} and command[index : index + 2] in {"||", "&&"}:
                separator_length = 2
            clauses.append(command[start:index].strip())
            index += separator_length
            start = index
            continue
        index += 1
    clauses.append(command[start:].strip())
    return clauses


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
    if command == "cd":
        return len(args) <= 1 and not any(arg.startswith("-") for arg in args)
    if command in {"pwd", "ls", "rg", "grep", "find", "head", "tail", "wc"}:
        return not any(arg in {"--delete", "-delete"} for arg in args)
    if command in {"env", "true"}:
        return not args
    if command in {"file", "readlink", "realpath", "stat", "test", "which"}:
        return True
    if command == "command":
        return len(args) == 2 and args[0] in {"-v", "-V"}
    if command == "echo":
        return all(not re.search(r"[`<>]", arg) for arg in args)
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
    if Path(command).name in {"python", "python3"}:
        if args in (["--version"], ["-V"]):
            return True
        if len(args) >= 3 and args[:2] == ["-m", "pip"] and args[2] == "show":
            return all(not argument.startswith("-") for argument in args[3:])
        return len(args) == 2 and args[0] == "-c" and _plan_python_probe_allowed(args[1])
    if Path(command).name in {"pip", "pip3"}:
        return (
            len(args) >= 2
            and args[0] == "show"
            and all(not argument.startswith("-") for argument in args[1:])
        )
    return False


_PLAN_PYTHON_PROBE_MODULES = {
    "curses",
    "json",
    "numpy",
    "os",
    "platform",
    "pygame",
    "pty",
    "sys",
}

_PLAN_PYTHON_SAFE_CALLS = {
    "abs",
    "all",
    "any",
    "bool",
    "dict",
    "enumerate",
    "float",
    "int",
    "isinstance",
    "len",
    "list",
    "max",
    "min",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "sorted",
    "str",
    "sum",
    "tuple",
    "zip",
}

_PLAN_PYTHON_SAFE_METHODS = {
    "append",
    "copy",
    "count",
    "endswith",
    "format",
    "get",
    "get_bitsize",
    "get_flags",
    "get_height",
    "get_size",
    "get_width",
    "index",
    "items",
    "join",
    "keys",
    "lower",
    "pop",
    "remove",
    "replace",
    "sort",
    "split",
    "startswith",
    "strip",
    "upper",
    "values",
}


def _attribute_root(node: ast.Attribute) -> str | None:
    value: ast.expr = node
    while isinstance(value, ast.Attribute):
        value = value.value
    return value.id if isinstance(value, ast.Name) else None


def _plan_python_call_allowed(call: ast.Call, local_functions: set[str]) -> bool:
    if isinstance(call.func, ast.Name):
        return call.func.id in _PLAN_PYTHON_SAFE_CALLS or call.func.id in local_functions
    if not isinstance(call.func, ast.Attribute) or call.func.attr.startswith("__"):
        return False
    root = _attribute_root(call.func)
    if root == "os":
        return call.func.attr in {"getcwd", "getenv", "getuid", "getgid"} or (
            isinstance(call.func.value, ast.Attribute)
            and call.func.value.attr == "environ"
            and call.func.attr in {"get", "setdefault"}
        )
    if root == "json":
        return call.func.attr in {"loads", "dumps"}
    if root == "platform":
        return True
    if root == "pygame":
        return call.func.attr not in {"save", "set_clipboard_text"}
    if root in _PLAN_PYTHON_PROBE_MODULES:
        return False
    return call.func.attr in _PLAN_PYTHON_SAFE_METHODS


def _plan_python_probe_allowed(source: str) -> bool:
    """Allow environment introspection without making Python a plan-mode escape hatch."""
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError:
        return False

    local_functions = {
        statement.name for statement in tree.body if isinstance(statement, ast.FunctionDef)
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(
                alias.name.split(".", 1)[0] not in _PLAN_PYTHON_PROBE_MODULES
                for alias in node.names
            ):
                return False
            continue
        if isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".", 1)[0] not in _PLAN_PYTHON_PROBE_MODULES:
                return False
            continue
        if isinstance(node, ast.Call) and not _plan_python_call_allowed(node, local_functions):
            return False
        if isinstance(node, (ast.Name, ast.Attribute)):
            name = node.id if isinstance(node, ast.Name) else node.attr
            if name.startswith("__") and name != "__version__":
                return False
    return bool(tree.body)


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
