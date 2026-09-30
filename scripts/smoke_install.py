"""Exercise an installed launcher with disposable state; never touch the user's gateway."""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx


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
            run("gateway", "restart")
            restarted = json.loads(run("gateway", "status"))
            assert restarted["healthy"] and restarted["pid"] != status["pid"]
        finally:
            run("gateway", "stop")
        stopped = json.loads(run("gateway", "status", expected_code=1))
        assert not stopped["running"] and not stopped["healthy"]
    print("Install smoke passed: doctor, gateway start/restart/stop, Web auth and bundled assets.")


if __name__ == "__main__":
    main()
