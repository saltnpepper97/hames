from __future__ import annotations

import logging
from pathlib import Path

import pytest

from hames.daemon import PidLock, read_pid
from hames.logging import configure_logging
from hames.paths import HamesPaths


def test_pid_lock_is_single_instance(hames_paths: HamesPaths) -> None:
    hames_paths.ensure_foundation()
    with PidLock(hames_paths.gateway_pid):
        assert read_pid(hames_paths) is not None
        with pytest.raises(RuntimeError, match="already held"):
            with PidLock(hames_paths.gateway_pid):
                pass
    assert not hames_paths.gateway_pid.exists()


def test_logging_redacts_sensitive_messages(tmp_path: Path) -> None:
    log_file = tmp_path / "hames.log"
    configure_logging(log_file)
    logging.getLogger("test").warning("Authorization: Bearer extremely-secret")
    content = log_file.read_text(encoding="utf-8")
    assert "extremely-secret" not in content
    assert "[redacted]" in content
