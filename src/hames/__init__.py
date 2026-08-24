"""Hames trusted backend."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("hames-harness")
except PackageNotFoundError:  # pragma: no cover - source tree without installation
    __version__ = "0.0.0"

PROTOCOL_VERSION = 17

__all__ = ["PROTOCOL_VERSION", "__version__"]
