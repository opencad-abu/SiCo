from pathlib import Path
import os

from mtsnetlistor.model_corners import model_corners


def test_model_corner_cache_scans_once_and_tracks_content_replacement(
    tmp_path, monkeypatch
):
    path = tmp_path / "model.lib"
    path.write_text(
        'section tt\nsection TT\n.lib "file name" ss ; comment\nsection=ff\n'
    )
    original = Path.open
    reads = []

    def counted(self, *args, **kwargs):
        if self == path:
            reads.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", counted)
    for _ in range(7):
        assert model_corners(str(path)) == ("tt", "ss", "ff")
    assert len(reads) == 1
    modified = path.stat().st_mtime_ns
    replacement = tmp_path / "replacement"
    replacement.write_text(
        'section xx\nsection XX\n.lib "file name" yy ; comment\nsection=zz\n'
    )
    os.utime(replacement, ns=(modified, modified))
    replacement.replace(path)
    assert model_corners(str(path)) == ("xx", "yy", "zz")
    assert len(reads) == 2
    path.unlink()
    assert model_corners(str(path)) == ()
    path.write_text(".lib slow\n")
    assert model_corners(str(path)) == ("slow",)


def test_in_place_edit_and_symlink_project_change_invalidate(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    link = tmp_path / "selected"
    a.write_text("section tt\n")
    b.write_text("section ss\n")
    link.symlink_to(a)
    assert model_corners(str(link)) == ("tt",)
    link.unlink()
    link.symlink_to(b)
    assert model_corners(str(link)) == ("ss",)
    stat = b.stat()
    b.write_text("section ff\n")
    os.utime(b, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    assert model_corners(str(link)) == ("ff",)
