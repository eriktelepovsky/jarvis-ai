"""Installing custom plugins listed in MYORB_PLUGINS into the data directory.

Packages go into <data_dir>/plugins, not into myOrb's own environment, so they survive
container updates and the container does not need write access to its own files.
"""

import logging
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def plugins_dir(data_dir: Path) -> Path:
    return data_dir / "plugins"


def add_to_path(directory: Path) -> None:
    """Make plugins installed in `directory` importable and discoverable.

    It goes last on sys.path: packages of myOrb's own environment win over copies a plugin
    may have pulled in as dependencies.
    """
    if directory.is_dir() and str(directory) not in sys.path:
        sys.path.append(str(directory))


def install_command(spec: str, directory: Path) -> list[str]:
    """The command that installs the packages in `spec` (as in MYORB_PLUGINS)."""
    packages = shlex.split(spec)
    uv = shutil.which("uv")
    if uv:
        return [
            uv,
            "pip",
            "install",
            "--quiet",
            "--python",
            sys.executable,
            "--target",
            str(directory),
            *packages,
        ]
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--quiet",
        "--target",
        str(directory),
        *packages,
    ]


def install(spec: str, directory: Path) -> None:
    if not shlex.split(spec):
        return
    directory.mkdir(parents=True, exist_ok=True)
    logger.warning("Installing plugins: %s", spec)
    subprocess.run(install_command(spec, directory), check=True)
