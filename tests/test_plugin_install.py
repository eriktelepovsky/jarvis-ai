import sys
from pathlib import Path

import pytest

from myorb.plugins import discover_plugins
from myorb.plugins.install import add_to_path, install, install_command

PLUGIN = """
from myorb.plugins import Plugin


class HelloPlugin(Plugin):
    id = "hello"
    name = "Hello"
"""


@pytest.fixture
def target(tmp_path: Path):
    """A plugin installed into a target directory, as `uv pip install --target` would."""
    target = tmp_path / "plugins"
    (target / "hello_plugin").mkdir(parents=True)
    (target / "hello_plugin" / "__init__.py").write_text(PLUGIN)
    dist = target / "hello_plugin-1.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: hello-plugin\nVersion: 1.0\n")
    (dist / "entry_points.txt").write_text("[myorb.plugins]\nhello = hello_plugin:HelloPlugin\n")
    yield target
    if str(target) in sys.path:
        sys.path.remove(str(target))
    sys.modules.pop("hello_plugin", None)


def test_installed_plugins_are_discovered(target: Path):
    assert "hello" not in discover_plugins()
    add_to_path(target)
    assert sys.path[-1] == str(target)
    assert discover_plugins()["hello"].name == "Hello"


def test_add_to_path_skips_missing_directory(tmp_path: Path):
    add_to_path(tmp_path / "missing")
    assert str(tmp_path / "missing") not in sys.path


def test_install_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/uv")
    command = install_command('myorb-wallet==1.2 "/plugins/my plugin"', tmp_path)
    assert command[:3] == ["/usr/local/bin/uv", "pip", "install"]
    assert command[-2:] == ["myorb-wallet==1.2", "/plugins/my plugin"]
    assert "--target" in command and str(tmp_path) in command

    monkeypatch.setattr("shutil.which", lambda name: None)
    assert install_command("x", tmp_path)[:3] == [sys.executable, "-m", "pip"]


def test_install_runs_the_command(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    calls = []
    monkeypatch.setattr("subprocess.run", lambda command, check: calls.append(command))
    install("   ", tmp_path / "plugins")
    assert calls == []
    install("myorb-wallet", tmp_path / "plugins")
    assert len(calls) == 1 and (tmp_path / "plugins").is_dir()
