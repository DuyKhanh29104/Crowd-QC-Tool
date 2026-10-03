from pathlib import Path

from backend.app.media.service import trim_cache


def test_eviction_tolerates_files_removed_during_scan(tmp_path, monkeypatch):
    vanished = tmp_path / 'vanished.png'
    survivor = tmp_path / 'survivor.png'
    vanished.write_bytes(b'123')
    survivor.write_bytes(b'456')
    original = Path.stat

    def stat(path, *args, **kwargs):
        if path == vanished:
            vanished.unlink(missing_ok=True)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', stat)
    trim_cache(tmp_path, 0)
    assert not list(tmp_path.glob('*.png'))


def test_eviction_skips_windows_busy_files(tmp_path, monkeypatch):
    busy = tmp_path / 'busy.png'
    free = tmp_path / 'free.png'
    busy.write_bytes(b'123')
    free.write_bytes(b'456')
    original = Path.unlink

    def unlink(path, *args, **kwargs):
        if path == busy:
            raise PermissionError('file is open in another worker')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', unlink)
    trim_cache(tmp_path, 0)
    assert busy.read_bytes() == b'123'
    assert not free.exists()
