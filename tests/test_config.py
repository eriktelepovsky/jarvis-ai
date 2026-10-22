from pathlib import Path

import pytest

from myorb.config import Settings, move_legacy_database


def test_jarvis_variables_still_work(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('JARVIS_TIMEZONE="Europe/Bratislava"\n')
    monkeypatch.setenv("JARVIS_PORT", "8123")
    monkeypatch.setenv("MYORB_HOST", "0.0.0.0")
    monkeypatch.setenv("JARVIS_HOST", "10.0.0.1")  # the new name wins
    for key in ("MYORB_PORT", "MYORB_TIMEZONE"):
        monkeypatch.delenv(key, raising=False)
    settings = Settings()
    assert (settings.port, settings.host, settings.timezone) == (
        8123,
        "0.0.0.0",
        "Europe/Bratislava",
    )


def test_database_file_is_renamed(tmp_path: Path):
    (tmp_path / "jarvis.db").write_text("data")
    move_legacy_database(tmp_path)
    assert (tmp_path / "myorb.db").read_text() == "data"
    assert not (tmp_path / "jarvis.db").exists()
    (tmp_path / "jarvis.db").write_text("old")
    move_legacy_database(tmp_path)  # an existing myorb.db is never replaced
    assert (tmp_path / "myorb.db").read_text() == "data"
