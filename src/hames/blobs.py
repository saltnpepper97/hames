"""Private content-addressed payload storage."""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path


class BlobIntegrityError(RuntimeError):
    pass


class BlobStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def path_for(self, digest: str) -> Path:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ValueError("invalid SHA-256 blob address")
        return self.root / "sha256" / digest[:2] / digest

    def put(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        target = self.path_for(digest)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        for directory in (self.root, self.root / "sha256", target.parent):
            directory.chmod(0o700)
        if target.exists():
            self.read(digest)
            return digest
        descriptor, temporary_name = tempfile.mkstemp(prefix=".hames-blob-", dir=target.parent)
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            target.chmod(0o600)
        finally:
            temporary.unlink(missing_ok=True)
        return digest

    def read(self, digest: str) -> bytes:
        target = self.path_for(digest)
        try:
            content = target.read_bytes()
        except FileNotFoundError as exc:
            raise BlobIntegrityError(f"missing blob: {digest}") from exc
        actual = hashlib.sha256(content).hexdigest()
        if actual != digest:
            raise BlobIntegrityError(f"corrupt blob: expected {digest}, found {actual}")
        return content
