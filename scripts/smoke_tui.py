"""Exercise the installed TUI through a real PTY with disposable gateway state."""

from __future__ import annotations

import fcntl
import json
import os
import pty
import select
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from hames.paths import HamesPaths
from hames.search_service import SearchService


class _ModelHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/props?model=fixture":
            body = b"{}"
        elif self.path == "/v1/models":
            body = json.dumps({"data": [{"id": "fixture", "status": "loaded"}]}).encode()
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/v1/responses":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        assert request["model"] == "fixture"
        assert "hello from PTY" in json.dumps(request["input"])
        events: list[dict[str, object]] = [
            {"type": "response.created", "response": {"id": "fixture-response"}},
            {"type": "response.output_text.delta", "delta": "fixture reply"},
            {"type": "response.completed", "response": {"output": []}},
        ]
        body = "\n\n".join(f"data: {json.dumps(event)}" for event in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def _read_until(fd: int, needle: bytes, *, seconds: float) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ready, _, _ = select.select([fd], [], [], min(0.2, deadline - time.monotonic()))
        if not ready:
            continue
        try:
            chunk = os.read(fd, 65_536)
        except OSError:
            break
        if not chunk:
            break
        output.extend(chunk)
        if needle in output:
            return bytes(output)
    raise AssertionError(f"TUI output did not contain {needle!r}: {bytes(output[-800:])!r}")


def _wait_for_exit(fd: int, pid: int, *, seconds: float) -> None:
    output = bytearray()
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        finished, status = os.waitpid(pid, os.WNOHANG)
        if finished:
            assert os.waitstatus_to_exitcode(status) == 0, (status, bytes(output[-800:]))
            return
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                output.extend(os.read(fd, 65_536))
            except OSError:
                pass
            if len(output) > 4_096:
                del output[:-4_096]
    raise AssertionError(f"TUI did not exit after Ctrl+Q: {bytes(output[-800:])!r}")


def main() -> None:
    binary = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="hames-tui-smoke-") as directory:
        root = Path(directory)
        model_server = HTTPServer(("127.0.0.1", 0), _ModelHandler)
        model_thread = threading.Thread(target=model_server.serve_forever, daemon=True)
        model_thread.start()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        model_port = model_server.server_port
        (root / "config.toml").write_text(
            f"[gateway]\nport = {port}\n"
            f"[providers.llama_cpp]\nbase_url = 'http://127.0.0.1:{model_port}'\n"
            "model = 'fixture'\n[memory]\nautomatic_extraction = false\n",
            encoding="utf-8",
        )
        SearchService(HamesPaths(root)).setup(enabled=False)
        environment = {
            **os.environ,
            "HAMES_HOME": str(root),
            "TERM": "xterm-256color",
        }
        pid, fd = pty.fork()
        if pid == 0:
            os.chdir(root)
            os.execve(str(binary), [str(binary), "tui"], environment)
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
            _read_until(fd, b"Trust this workspace?", seconds=15)
            os.write(fd, b"y")
            startup = _read_until(fd, b"\x1b[?1049h", seconds=15)
            assert b"Trusted" in startup
            os.write(fd, b"hello from PTY\r")
            _read_until(fd, b"fixture reply", seconds=30)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 28, 90, 0, 0))
            os.kill(pid, signal.SIGWINCH)
            os.write(fd, b"\x11")  # Ctrl+Q
            _wait_for_exit(fd, pid, seconds=15)
        finally:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                pass
            os.close(fd)
            subprocess.run(
                [str(binary), "gateway", "stop"],
                env=environment,
                cwd=root,
                capture_output=True,
                check=True,
                timeout=20,
            )
            model_server.shutdown()
            model_server.server_close()
            model_thread.join(timeout=5)
    print("TUI PTY smoke passed: trust, provider reply, resize, Ctrl+Q.")


if __name__ == "__main__":
    main()
