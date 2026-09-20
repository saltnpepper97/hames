"""Small saved recipes for ordinary coordinator conversations."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class FlowParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    agent: str = Field(min_length=1, max_length=80)
    instructions: str = Field(default="", max_length=8000)


class FlowRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    coordinator: str = Field(min_length=1, max_length=80)
    instructions: str = Field(default="", max_length=16000)
    participants: list[FlowParticipant] | None = Field(default=None, max_length=32)


class FlowRecipeStore:
    def __init__(self, root: Path):
        self.root = root / "flow-recipes"

    def path(self, identifier: str) -> Path:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,62}", identifier):
            raise ValueError("Identifier must use lowercase letters, numbers, and hyphens")
        return self.root / f"{identifier}.json"

    def get(self, identifier: str) -> FlowRecipe:
        return FlowRecipe.model_validate_json(self.path(identifier).read_text())

    def list(self) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                items.append(
                    {"id": path.stem, "recipe": self.get(path.stem).model_dump(), "error": ""}
                )
            except (ValueError, OSError) as exc:
                items.append({"id": path.stem, "recipe": None, "error": str(exc)})
        return items

    def save(self, identifier: str, recipe: FlowRecipe) -> None:
        path = self.path(identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        temporary.write_text(json.dumps(recipe.model_dump(), indent=2) + "\n")
        os.replace(temporary, path)

    def delete(self, identifier: str) -> None:
        self.path(identifier).unlink()
