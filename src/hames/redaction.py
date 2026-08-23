"""Deterministic pre-persistence secret redaction."""

from __future__ import annotations

import copy
from collections.abc import Iterable

from hames.providers.base import JsonValue

SENSITIVE_KEYS = frozenset({"authorization", "api_key", "api-key", "x-api-key"})
REDACTED: dict[str, JsonValue] = {"$redacted": True, "reason": "secret"}


class RedactionError(ValueError):
    pass


def redact(
    payload: dict[str, JsonValue], secret_paths: Iterable[str] = ()
) -> tuple[dict[str, JsonValue], bool]:
    value = _redact_mapping(copy.deepcopy(payload))
    changed = value != payload
    for pointer in secret_paths:
        _redact_pointer(value, pointer)
        changed = True
    return value, changed


def _redact_mapping(value: dict[str, JsonValue]) -> dict[str, JsonValue]:
    return {
        key: copy.deepcopy(REDACTED) if key.lower() in SENSITIVE_KEYS else _redact_known_keys(item)
        for key, item in value.items()
    }


def _redact_known_keys(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return _redact_mapping(value)
    if isinstance(value, list):
        return [_redact_known_keys(item) for item in value]
    return value


def _redact_pointer(payload: dict[str, JsonValue], pointer: str) -> None:
    if not pointer.startswith("/"):
        raise RedactionError("secret path must be an RFC 6901 JSON pointer")
    components = [part.replace("~1", "/").replace("~0", "~") for part in pointer[1:].split("/")]
    current: JsonValue = payload
    for component in components[:-1]:
        if isinstance(current, dict) and component in current:
            current = current[component]
        elif isinstance(current, list) and component.isdigit() and int(component) < len(current):
            current = current[int(component)]
        else:
            raise RedactionError(f"secret path does not exist: {pointer}")
    leaf = components[-1]
    if isinstance(current, dict) and leaf in current:
        current[leaf] = copy.deepcopy(REDACTED)
    elif isinstance(current, list) and leaf.isdigit() and int(leaf) < len(current):
        current[int(leaf)] = copy.deepcopy(REDACTED)
    else:
        raise RedactionError(f"secret path does not exist: {pointer}")
