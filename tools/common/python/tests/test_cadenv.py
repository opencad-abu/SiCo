from __future__ import annotations

import pytest

from cadenv import (
    cadence_mps_environment_names,
    detach_cadence_mps_environment,
    preserve_eda_temp_environment,
    restore_eda_temp_environment,
)


def test_preserve_and_restore_exact_eda_temp_environment() -> None:
    environment = {
        "TMPDIR": "/site/tmp",
        "TEMP": "",
        "XDG_CACHE_HOME": "/site/cache",
    }

    preserve_eda_temp_environment(environment)
    environment.update(
        {
            "SICO_TEMP_DIR": "/work/.sico",
            "TMPDIR": "/work/.sico",
            "TMP": "/work/.sico",
            "TEMP": "/work/.sico",
            "SQLITE_TMPDIR": "/work/.sico",
            "XDG_CACHE_HOME": "/work/.sico/cache",
            "XDG_RUNTIME_DIR": "/work/.sico/runtime",
        }
    )

    restored = restore_eda_temp_environment(environment)

    assert restored["TMPDIR"] == "/site/tmp"
    assert restored["TEMP"] == ""
    assert restored["XDG_CACHE_HOME"] == "/site/cache"
    assert all(
        name not in restored
        for name in ("CAD_TEMP_DIR", "TMP", "SQLITE_TMPDIR", "XDG_RUNTIME_DIR")
    )
    assert not any(name.startswith("CAD_ORIG_") for name in restored)


def test_restore_removes_uncaptured_sico_temp_injection() -> None:
    restored = restore_eda_temp_environment(
        {
            "SICO_TEMP_DIR": "/work/.sico",
            "TMPDIR": "/work/.sico",
            "TMP": "/work/.sico",
            "TEMP": "/work/.sico",
            "SQLITE_TMPDIR": "/work/.sico",
            "XDG_CACHE_HOME": "/work/.sico/cache",
            "XDG_RUNTIME_DIR": "/work/.sico/runtime",
            "KEEP_ME": "yes",
        }
    )

    assert restored == {"KEEP_ME": "yes"}


def test_restore_keeps_direct_cli_temp_environment() -> None:
    environment = {"TMPDIR": "/site/tmp", "XDG_CACHE_HOME": "/site/cache"}

    assert restore_eda_temp_environment(environment) == environment


def test_detach_cadence_mps_environment_covers_future_selectors() -> None:
    environment = {
        "PATH": "/usr/bin",
        "CDS_MPS_SESSION": "virtuoso405942",
        "CDS_MPS_FUTURE_SELECTOR": "future",
    }
    expected = ("CDS_MPS_FUTURE_SELECTOR", "CDS_MPS_SESSION")
    assert cadence_mps_environment_names(environment) == expected
    assert detach_cadence_mps_environment(environment) == expected
    assert environment == {"PATH": "/usr/bin"}


def test_sico_markers_preserve_vendor_environment_and_are_removed():
    environment = {"SICO_TEMP_DIR": "/work/.sico", "TMPDIR": "/vendor/tmp", "TEMP": ""}
    preserve_eda_temp_environment(environment)
    assert environment["SICO_ORIG_TMPDIR"] == "/vendor/tmp"
    assert environment["SICO_ORIG_TEMP_SET"] == "1"
    environment.update(TMPDIR="/work/.sico/runtime", TEMP="/work/.sico/runtime")
    restored = restore_eda_temp_environment(environment)
    assert restored == {"TMPDIR": "/vendor/tmp", "TEMP": ""}


def test_new_markers_are_atomic_and_do_not_borrow_legacy_values():
    environment = {
        "SICO_TEMP_DIR": "/work/.sico",
        "SICO_ORIG_TMPDIR_SET": "1",
        "CAD_ORIG_TMPDIR_SET": "1",
        "CAD_ORIG_TMPDIR": "/wrong/legacy/value",
        "TMPDIR": "/work/.sico/runtime",
    }
    assert restore_eda_temp_environment(environment) == {"TMPDIR": ""}


def test_retired_markers_reject_capture_and_restore_without_mutation():
    environment = {
        "SICO_TEMP_DIR": "/work/.sico",
        "CAD_ORIG_TMPDIR_SET": "1",
        "CAD_ORIG_TMPDIR": "/vendor/tmp",
        "TMPDIR": "/work/.sico",
    }
    original = dict(environment)
    for operation in (preserve_eda_temp_environment, restore_eda_temp_environment):
        with pytest.raises(ValueError, match="CAD_ORIG_TMPDIR_SET was removed"):
            operation(environment)
        assert environment == original


def test_flows_share_the_authoritative_eda_restoration():
    from lefpy.environment import restore_eda_temp_environment as lef_restore
    from rcepy.pathutil import restore_eda_temp_environment as rce_restore

    assert lef_restore is restore_eda_temp_environment
    assert rce_restore is restore_eda_temp_environment


def test_eda_provenance_preserves_empty_unset_and_is_idempotent():
    from cadenv import preserve_virtuoso_environment, restore_virtuoso_environment

    source = {"PATH": "/eda/bin", "PYTHONPATH": "", "LM_LICENSE_FILE": "fixture"}
    original = dict(source)
    preserve_virtuoso_environment(source)
    assert source["SICO_VIRTUOSO_PYTHONPATH_SET"] == "1"
    assert source["SICO_VIRTUOSO_LD_LIBRARY_PATH_SET"] == "0"
    source.update(PATH="/runtime/bin", LD_LIBRARY_PATH="/runtime/lib", PYTHONPATH="/runtime/python")
    captured = dict(source)
    preserve_virtuoso_environment(source)
    assert source == captured
    assert restore_virtuoso_environment(source) == original
    assert source == captured


@pytest.mark.parametrize("invalid", [
    {"SICO_VIRTUOSO_PATH_SET": "1"},
    {"SICO_VIRTUOSO_PATH": "", "SICO_VIRTUOSO_PATH_SET": "invalid"},
    {"SICO_VIRTUOSO_PATH": "/eda/bin", "SICO_VIRTUOSO_PATH_SET": "0"},
    {"CAD_VIRTUOSO_PATH_SET": "0"},
])
def test_invalid_eda_provenance_never_mutates_the_input(invalid):
    from cadenv import preserve_virtuoso_environment, restore_virtuoso_environment

    original = dict(invalid)
    for operation in (preserve_virtuoso_environment, restore_virtuoso_environment):
        with pytest.raises(ValueError):
            operation(invalid)
        assert invalid == original


def test_old_eda_capture_is_not_reinterpreted_as_unset():
    from cadenv import preserve_virtuoso_environment, restore_virtuoso_environment

    source = {"PATH": "/runtime/bin", "SICO_VIRTUOSO_PATH": ""}
    preserve_virtuoso_environment(source)
    assert "SICO_VIRTUOSO_PATH_SET" not in source
    assert restore_virtuoso_environment(source)["PATH"] == ""
