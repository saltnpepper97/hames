"""Exercise an installed launcher with disposable state; never touch the user's gateway."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from hames.paths import HamesPaths


def _macos_picker_smoke(root: Path, port: int) -> None:
    workspace = root / "picker-workspace"
    workspace.mkdir()
    token = HamesPaths(root).read_gateway_token()
    url = f"http://127.0.0.1:{port}/v1/directories/select"
    script = """
tell application "System Events"
    repeat 100 times
        repeat with picker in (every process whose name is "osascript")
            if (count of windows of picker) > 0 then
                set frontmost of picker to true
                key code 36
                return "selected"
            end if
        end repeat
        delay 0.1
    end repeat
end tell
error "Hames folder picker did not appear"
"""
    with httpx.Client(timeout=30) as client, ThreadPoolExecutor(max_workers=1) as pool:
        selected = pool.submit(
            client.post,
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"initial_path": str(workspace)},
        )
        driven = subprocess.run(
            ["/usr/bin/osascript", "-e", script],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert driven.returncode == 0, driven.stderr
        response = selected.result(timeout=30)
    assert response.status_code == 200, response.text
    assert response.json() == {"path": str(workspace)}


def main() -> None:
    binary = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="hames-install-smoke-") as directory:
        root = Path(directory)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        (root / "config.toml").write_text(f"[gateway]\nport = {port}\n", encoding="utf-8")
        environment = {**os.environ, "HAMES_HOME": str(root)}

        def run(*arguments: str, expected_code: int = 0) -> str:
            result = subprocess.run(
                [str(binary), *arguments],
                env=environment,
                cwd=root,
                check=False,
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert result.returncode == expected_code, (arguments, result.stderr)
            return result.stdout

        try:
            assert "hames " in run("--version")
            doctor = json.loads(run("doctor"))
            assert doctor["healthy"], doctor
            if sys.platform == "darwin":
                assert not doctor["bubblewrap"]
                assert doctor["macos_sandbox"]
                assert not doctor["limitations"]
            run("gateway", "start")
            status = json.loads(run("gateway", "status"))
            assert status["healthy"] and status["pid"]
            launch = run("web", "--no-open")
            url = next(
                line.removeprefix("Open ")
                for line in launch.splitlines()
                if line.startswith("Open ")
            )
            with httpx.Client(follow_redirects=True, timeout=10) as client:
                page = client.get(url)
                assert page.status_code == 200
                assert "Hames" in page.text
                assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', page.text)
                assert assets, "packaged Web assets missing"
                for asset in assets:
                    assert client.get(f"http://127.0.0.1:{port}{asset}").status_code == 200
                bootstrap = client.get(f"http://127.0.0.1:{port}/_hames/v1/bootstrap")
                assert bootstrap.status_code == 200
            if sys.platform == "darwin":
                _macos_picker_smoke(root, port)
            run("gateway", "restart")
            restarted = json.loads(run("gateway", "status"))
            assert restarted["healthy"] and restarted["pid"] != status["pid"]
        finally:
            run("gateway", "stop")
        stopped = json.loads(run("gateway", "status", expected_code=1))
        assert not stopped["running"] and not stopped["healthy"]
    print(
        "Install smoke passed: doctor, gateway start/restart/stop, Web auth and "
        "bundled assets, and native Mac folder picker when available."
    )


if __name__ == "__main__":
    main()
