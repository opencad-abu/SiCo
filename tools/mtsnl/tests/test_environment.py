from __future__ import annotations

from pathlib import Path

import pytest

from mtsnetlistor.environment import (
    SessionDescriptor,
    detach_mps_environment,
    isolated_environment,
    write_cds_lib_overlay,
)
from mtsnetlistor.errors import IsolationError
import hashlib


def test_isolated_environment_replaces_inherited_cadence_selectors(tmp_path: Path) -> None:
    source = tmp_path / "source.cds.lib"
    source.write_text("DEFINE work ./work\n", encoding="utf-8")
    env = isolated_environment(
        {
            "PATH": "/usr/bin",
            "CDS_LIB": "/target/cds.lib",
            "CDS_CDSLIB": "/target/cds.lib",
            "CDS_MPS_SESSION": "virtuoso405942",
            "CDS_MPS_HOST": "host.example",
            "CDS_MPS_PORT": "405942",
            "CDS_MPS_FUTURE_SELECTOR": "must-not-leak",
            "MTS_NETLISTOR_TARGET_CDSLIB": "/target/cds.lib",
        },
        cds_lib=source,
        workdir=tmp_path / "run",
    )
    assert env["CDS_LIB"] == str(source.resolve())
    assert env["CDS_CDSLIB"] == str(source.resolve())
    assert env["MTS_NETLISTOR_CDSLIB"] == str(source.resolve())
    assert "MTS_NETLISTOR_TARGET_CDSLIB" not in env
    assert not any(name.startswith("CDS_MPS_") for name in env)


def test_detach_mps_environment_removes_complete_namespace() -> None:
    environment = {
        "PATH": "/usr/bin",
        "CDS_MPS_SESSION": "virtuoso405942",
        "CDS_MPS_HOST": "work-srv",
        "CDS_MPS_FUTURE_SELECTOR": "future",
    }
    removed = detach_mps_environment(environment)
    assert removed == (
        "CDS_MPS_FUTURE_SELECTOR",
        "CDS_MPS_HOST",
        "CDS_MPS_SESSION",
    )
    assert environment == {"PATH": "/usr/bin"}


def test_isolated_environment_rejects_reintroducing_mps_selector(tmp_path: Path) -> None:
    source = tmp_path / "source.cds.lib"
    source.write_text("DEFINE work ./work\n", encoding="utf-8")
    with pytest.raises(IsolationError, match="reserved child environment key"):
        isolated_environment(
            {},
            cds_lib=source,
            workdir=tmp_path / "run",
            extra={"CDS_MPS_SESSION": "virtuoso405942"},
        )


def test_isolated_environment_rejects_forbidden_selector(tmp_path: Path) -> None:
    source = tmp_path / "source.cds.lib"
    target = tmp_path / "target.cds.lib"
    source.write_text("DEFINE work ./work\n", encoding="utf-8")
    target.write_text("DEFINE work ./target\n", encoding="utf-8")
    with pytest.raises(IsolationError, match="must not share"):
        isolated_environment(
                {}, cds_lib=source, workdir=tmp_path / "run", forbidden_cds_lib=source
        )


def test_write_cds_lib_overlay_is_single_domain_and_atomic(tmp_path: Path) -> None:
    source = tmp_path / "source.cds.lib"
    source.write_text("DEFINE work ./work\n", encoding="utf-8")
    transfer = tmp_path / "transfer"
    overlay = write_cds_lib_overlay(
        source,
        tmp_path / "run" / "source-overlay.cds.lib",
        define=("MTS_XFER", transfer),
        forbidden_paths=(tmp_path / "target.cds.lib",),
    )
    assert overlay.read_text(encoding="utf-8") == (
        f"INCLUDE {source.resolve()}\nDEFINE MTS_XFER {transfer.resolve()}\n"
    )
    assert transfer.is_dir()
    write_cds_lib_overlay(source, overlay)
    assert overlay.read_text(encoding="utf-8") == f"INCLUDE {source.resolve()}\n"


def test_write_cds_lib_overlay_rejects_forbidden_base(tmp_path: Path) -> None:
    source = tmp_path / "source.cds.lib"
    source.write_text("DEFINE work ./work\n", encoding="utf-8")
    with pytest.raises(IsolationError, match="forbidden domain"):
        write_cds_lib_overlay(source, tmp_path / "overlay.cds.lib", forbidden_paths=(source,))


def test_session_validation_rejects_exited_owner_with_numeric_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    digest = hashlib.sha256(cds.read_bytes()).hexdigest()
    descriptor = SessionDescriptor(
        owner_pid=4242,
        owner_start_time="12345",
        target_cds_lib=cds,
        target_cds_lib_digest=digest,
        target_library_paths={},
    )
    monkeypatch.setattr("mtsnetlistor.environment._process_start_time", lambda _pid: None)
    with pytest.raises(IsolationError, match="no longer alive|process exited"):
        descriptor.validate()


def test_session_validation_rejects_pid_reuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    digest = hashlib.sha256(cds.read_bytes()).hexdigest()
    descriptor = SessionDescriptor(
        owner_pid=4242,
        owner_start_time="12345",
        target_cds_lib=cds,
        target_cds_lib_digest=digest,
        target_library_paths={},
    )
    monkeypatch.setattr("mtsnetlistor.environment._process_start_time", lambda _pid: "99999")
    with pytest.raises(IsolationError, match="PID was reused"):
        descriptor.validate()


def test_session_validation_accepts_matching_numeric_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cds = tmp_path / "cds.lib"
    cds.write_text("DEFINE work ./work\n", encoding="utf-8")
    digest = hashlib.sha256(cds.read_bytes()).hexdigest()
    descriptor = SessionDescriptor(
        owner_pid=4242,
        owner_start_time="12345",
        target_cds_lib=cds,
        target_cds_lib_digest=digest,
        target_library_paths={},
    )
    monkeypatch.setattr("mtsnetlistor.environment._process_start_time", lambda _pid: "12345")
    assert descriptor.validate().owner_pid == 4242
