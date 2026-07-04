from r2db import FilesystemTarget, backup_file, restore_file


def test_backup_then_restore_roundtrips(tmp_path):
    target = FilesystemTarget(tmp_path / "bucket")
    src = tmp_path / "app.sqlite"
    src.write_bytes(b"SQLite format 3\x00payload")

    n = backup_file(target, src, "state/app.sqlite")
    assert n == len(src.read_bytes())

    dest = tmp_path / "restored.sqlite"
    assert restore_file(target, dest, "state/app.sqlite") is True
    assert dest.read_bytes() == src.read_bytes()


def test_restore_missing_returns_false(tmp_path):
    target = FilesystemTarget(tmp_path / "bucket")
    dest = tmp_path / "restored.sqlite"
    assert restore_file(target, dest, "state/none.sqlite") is False
    assert not dest.exists()
