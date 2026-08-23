from __future__ import annotations

from pathlib import Path

import pytest

from hames.paths import HamesPaths


@pytest.fixture
def hames_paths(tmp_path: Path) -> HamesPaths:
    return HamesPaths.resolve(root=tmp_path / "hames-home")
