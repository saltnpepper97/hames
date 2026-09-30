"""Exercise the optional macOS login agent with disposable Hames state."""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

from hames.daemon import gateway_status, start, stop
from hames.launchd import agent_path, install, remove
from hames.paths import HamesPaths


def main() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("launchd smoke requires macOS")
    if agent_path().exists():
        raise RuntimeError(f"refusing to replace existing LaunchAgent: {agent_path()}")
    with tempfile.TemporaryDirectory(prefix="hames-launchd-") as directory:
        paths = HamesPaths(Path(directory) / "home")
        os.environ["HAMES_HOME"] = str(paths.root)
        try:
            install(paths)
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not gateway_status(paths).healthy:
                time.sleep(0.1)
            assert gateway_status(paths).healthy, "LaunchAgent did not start gateway"
            assert not stop(paths).running, "gateway remained running after launchd bootout"
            assert start(paths).healthy, "gateway did not restart through launchd"
        finally:
            stop(paths)
            remove()


if __name__ == "__main__":
    main()
