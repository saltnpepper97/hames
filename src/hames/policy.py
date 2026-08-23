"""Deterministic M03 policy gate for core tools."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from hames.providers.base import JsonValue
from hames.tools import ShellArguments, ToolArguments, ToolContext, WorkspaceArguments


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

_OUTSIDE_PATH = re.compile(r"(?<![\w$])/(?:home|root|etc|var|opt|srv|mnt|media)/[^\s'\";&|]+")


class PolicyGate:
    def __init__(self, protected_root: Path) -> None:
        self.protected_root = protected_root.expanduser().resolve(strict=False)

    def decide(
        self,
        tool_name: str,
        arguments: ToolArguments,
        context: ToolContext,
    ) -> PolicyDecision:
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

        if isinstance(arguments, ShellArguments):
            for pattern, reason in _DENIED_SHELL:
                if pattern.search(arguments.command):
                    return PolicyDecision(PolicyDecisionKind.DENY, reason, "prohibited")
            for pattern, reason in _CONFIRM_SHELL:
                if pattern.search(arguments.command):
                    return PolicyDecision(PolicyDecisionKind.REQUIRE_CONFIRMATION, reason, "high")
            workspace_root = context.root_for(arguments.workspace).resolve(strict=True)
            for raw_path in _OUTSIDE_PATH.findall(arguments.command):
                candidate = Path(raw_path).expanduser().resolve(strict=False)
                if candidate != workspace_root and not candidate.is_relative_to(workspace_root):
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

        return PolicyDecision(PolicyDecisionKind.ALLOW, "allowed by trusted-root policy")


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
