"""Narrow macOS Seatbelt profile for untrusted plugin and Skill processes.

The profile is intentionally deny-by-default. Paths are canonicalized and
quoted before inclusion so a workspace name cannot alter the policy.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from hames.platform_support import macos_sandbox_path, sandbox_unavailable_reason


def _subpath(path: Path) -> str:
    return f"(subpath {json.dumps(str(path.resolve()))})"


def isolated_command(
    *,
    executable: Path,
    arguments: list[str],
    package: Path,
    scratch: Path,
    project: Path | None = None,
    env_root: Path | None = None,
) -> list[str]:
    sandbox = macos_sandbox_path()
    if sandbox is None:
        raise OSError(sandbox_unavailable_reason())
    scratch.mkdir(mode=0o700, parents=True, exist_ok=True)
    scratch = scratch.resolve()
    requested_executable = executable.absolute()
    executable = executable.resolve()
    # Python installed by uv/Homebrew can live outside the sealed system tree.
    # Only its own runtime tree is readable, never the rest of the home folder.
    runtime_roots = {
        Path(sys.base_prefix).resolve(),
        executable.parent,
        requested_executable.parent,
    }
    if (requested_executable.parent.parent / "pyvenv.cfg").is_file():
        runtime_roots.add(requested_executable.parent.parent)
    if (
        executable.name.startswith("python")
        and executable.parent.name == "bin"
        and executable.parent.parent not in {Path("/usr"), Path("/usr/local")}
    ):
        runtime_roots.add(executable.parent.parent)
    read_roots = {
        Path("/System"),
        Path("/usr/bin"),
        Path("/usr/lib"),
        Path("/usr/share"),
        Path("/bin"),
        Path("/Library/Developer/CommandLineTools"),
        package.resolve(),
        scratch,
        *runtime_roots,
    }
    if project is not None:
        read_roots.add(project.resolve())
    if env_root is not None:
        read_roots.add(env_root.resolve())
    ancestors = {parent for root in read_roots for parent in root.parents}
    profile = "\n".join(
        [
            "(version 1)",
            "(deny default)",
            '(import "system.sb")',
            "(allow process*)",
            "(allow sysctl-read)",
            "(allow file-read-metadata "
            + " ".join(f"(literal {json.dumps(str(path))})" for path in sorted(ancestors))
            + ")",
            '(allow file-read-data (literal "/"))',
            "(allow file-read* " + " ".join(sorted(map(_subpath, read_roots))) + ")",
            f"(allow file-write* {_subpath(scratch)})",
        ]
    )
    return [
        "/usr/bin/env",
        "-i",
        "PATH=/usr/bin:/bin",
        f"HOME={scratch}",
        f"TMPDIR={scratch}{os.sep}",
        "PYTHONNOUSERSITE=1",
        "PYTHONUNBUFFERED=1",
        sandbox,
        "-p",
        profile,
        str(requested_executable),
        *arguments,
    ]
