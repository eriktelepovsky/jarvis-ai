from pathlib import Path

from myorb.config import move_legacy_database


def test_database_file_is_renamed(tmp_path: Path):
    (tmp_path / "jarvis.db").write_text("data")
    move_legacy_database(tmp_path)
    assert (tmp_path / "myorb.db").read_text() == "data"
    assert not (tmp_path / "jarvis.db").exists()
    (tmp_path / "jarvis.db").write_text("old")
    move_legacy_database(tmp_path)  # an existing myorb.db is never replaced
    assert (tmp_path / "myorb.db").read_text() == "data"
