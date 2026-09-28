from __future__ import annotations

from rcepy.pathutil import (
    cad_temp_environment,
    preserve_eda_temp_environment,
    restore_eda_temp_environment,
)


def test_cad_environment_round_trip_preserves_eda_temp_settings(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    source = {
        "TMPDIR": "/site/tmp",
        "TEMP": "",
        "XDG_CACHE_HOME": "/site/cache",
    }

    cad_environment = cad_temp_environment(source)
    restored = restore_eda_temp_environment(cad_environment)

    assert restored["TMPDIR"] == "/site/tmp"
    assert restored["TEMP"] == ""
    assert restored["XDG_CACHE_HOME"] == "/site/cache"
    assert all(
        name not in restored
        for name in ("CAD_TEMP_DIR", "TMP", "SQLITE_TMPDIR", "XDG_RUNTIME_DIR")
    )
    assert not any(name.startswith("CAD_ORIG_") for name in restored)


def test_restore_removes_legacy_cad_temp_injection() -> None:
    restored = restore_eda_temp_environment(
        {
            "CAD_TEMP_DIR": "/work/.cad",
            "TMPDIR": "/work/.cad",
            "TMP": "/work/.cad",
            "TEMP": "/work/.cad",
            "SQLITE_TMPDIR": "/work/.cad",
            "XDG_CACHE_HOME": "/work/.cad/cache",
            "XDG_RUNTIME_DIR": "/work/.cad/runtime",
            "KEEP_ME": "yes",
        }
    )

    assert restored == {"KEEP_ME": "yes"}


def test_preserve_does_not_replace_existing_frontend_markers() -> None:
    environment = {
        "CAD_TEMP_DIR": "/work/.cad",
        "TMPDIR": "/work/.cad",
        "CAD_ORIG_TMPDIR_SET": "1",
        "CAD_ORIG_TMPDIR": "/site/tmp",
    }

    preserve_eda_temp_environment(environment)

    assert environment["CAD_ORIG_TMPDIR_SET"] == "1"
    assert environment["CAD_ORIG_TMPDIR"] == "/site/tmp"
