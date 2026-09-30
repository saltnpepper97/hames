"""Exercise the optional macOS login agent with disposable Hames state."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from hames.daemon import gateway_status
from hames.launchd import agent_path
from hames.paths import HamesPaths


def main() -> None:
    if sys.platform != "darwin":
        raise RuntimeError("launchd smoke requires macOS")
    if agent_path().exists():
        raise RuntimeError(f"refusing to replace existing LaunchAgent: {agent_path()}")
    binary = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="hames-launchd-") as directory:
        paths = HamesPaths(Path(directory) / "home")
        os.environ["HAMES_HOME"] = str(paths.root)

        def run(*args: str) -> str:
            completed = subprocess.run(
                [str(binary), *args],
                cwd=directory,
                env=os.environ,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            assert completed.returncode == 0, (args, completed.stderr)
            return completed.stdout

        try:
            run("gateway", "service", "install")
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and not gateway_status(paths).healthy:
                time.sleep(0.1)
            assert gateway_status(paths).healthy, "LaunchAgent did not start gateway"
            run("gateway", "stop")
            assert not gateway_status(paths).running, (
                "gateway remained running after launchd bootout"
            )
            run("gateway", "start")
            assert gateway_status(paths).healthy, "gateway did not restart through launchd"
            assert "installed, loaded" in run("gateway", "service", "status")
        finally:
            run("gateway", "stop")
            run("gateway", "service", "remove")


if __name__ == "__main__":
    main()
