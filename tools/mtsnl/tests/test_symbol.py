from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from mtsnetlistor.environment import SessionDescriptor
from mtsnetlistor.errors import IsolationError, RequestValidationError, SymbolTransferError
from mtsnetlistor.model import NetlistRequest, SourceDesign, TargetSelection
from mtsnetlistor.symbol import transfer_symbol


def _request(source_cds: Path, *, symbol: bool = True, target_cell: str = "inv_mts") -> NetlistRequest:
    return NetlistRequest(
        source=SourceDesign(source_cds, "source", "inv", "schematic"),
        target=TargetSelection(
            "target",
            target_cell,
            generate_symbol_view=symbol,
        ),
    ).validate()


def _session(target_cds: Path, target_library: Path) -> SessionDescriptor:
    return SessionDescriptor(
        owner_pid=1,
        owner_start_time="test",
        target_cds_lib=target_cds,
        target_cds_lib_digest=hashlib.sha256(target_cds.read_bytes()).hexdigest(),
        target_library_paths={"target": str(target_library)},
    )


def _fake_dbaccess(
    path: Path,
    *,
    fail_stage: str = "",
    terminals: str = '("A" "Y" "VSS" "VDD")',
    source_transfer_terminals: str | None = None,
    target_terminals: str | None = None,
) -> Path:
    path.write_text(
        """#!/usr/bin/env python3
import json
import os
import pathlib
import sys

terminals = %r
source_transfer_terminals = %r
target_terminals = %r
args = sys.argv[1:]
cdslib = pathlib.Path(args[args.index('-cdslib') + 1]).resolve()
root = pathlib.Path(os.environ['MTS_TRANSFER_ROOT'])
stage = 'source' if 'MTS_SOURCE_LIB' in os.environ else 'target'
(root / (stage + '-env.json')).write_text(json.dumps(dict(os.environ), sort_keys=True), encoding='utf-8')
report = pathlib.Path(os.environ['MTS_TRANSFER_REPORT'])
transfer = root / 'inv' / 'symbol'
if stage == 'source':
    transfer.mkdir(parents=True, exist_ok=True)
    (transfer / 'shape.marker').write_text('oa', encoding='utf-8')
    report.write_text(
        'stage=source\\n'
        'cds_lib=' + str(cdslib) + '\\n'
        'source=source/inv/symbol\\n'
        'transfer=MTS_XFER/inv/symbol\\n'
        'source_terminals=' + terminals + '\\n'
        'transfer_terminals=' + source_transfer_terminals + '\\n'
        'copy_result=t\\n', encoding='utf-8')
else:
    target = pathlib.Path(os.environ['MTS_TARGET_CELL'])
    target_view = pathlib.Path(os.environ['MTS_TARGET_ROOT']) / str(target) / 'symbol'
    target_existed = target_view.exists()
    target_view.mkdir(parents=True, exist_ok=True)
    (target_view / 'shape.marker').write_text('oa', encoding='utf-8')
    report.write_text(
        'stage=target\\n'
        'cds_lib=' + str(cdslib) + '\\n'
        'target=target/' + str(target) + '/symbol\\n'
        'transfer=MTS_XFER/inv/symbol\\n'
        'transfer_terminals=' + terminals + '\\n'
        'target_terminals=' + target_terminals + '\\n'
        'overwrite=' + ('t' if os.environ['MTS_TARGET_OVERWRITE'] == '1' else 'nil') + '\\n'
        'target_existed=' + ('t' if target_existed else 'nil') + '\\n'
        'copy_result=t\\n', encoding='utf-8')
if stage == %r:
    raise SystemExit(9)
""" % (
            terminals,
            terminals if source_transfer_terminals is None else source_transfer_terminals,
            terminals if target_terminals is None else target_terminals,
            fail_stage,
        ),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, SessionDescriptor, NetlistRequest]:
    source_cds = tmp_path / "source.cds.lib"
    target_cds = tmp_path / "target.cds.lib"
    source_lib = tmp_path / "source-lib"
    target_lib = tmp_path / "target-lib"
    source_lib.mkdir()
    target_lib.mkdir()
    source_cds.write_text(f"DEFINE source {source_lib}\n", encoding="utf-8")
    target_cds.write_text(f"DEFINE target {target_lib}\n", encoding="utf-8")
    session = _session(target_cds, target_lib)
    return source_cds, target_cds, target_lib, session, _request(source_cds)


def test_transfer_symbol_runs_source_then_target_with_disjoint_domains(tmp_path: Path) -> None:
    source_cds, _target_cds, target_lib, session, request = _setup(tmp_path)
    dbaccess = _fake_dbaccess(tmp_path / "dbAccess")
    result = transfer_symbol(request, session, run_dir=tmp_path / "run", dbaccess=str(dbaccess))

    assert result.status == "succeeded"
    assert result.source_process.pid != result.target_process.pid
    assert result.terminal_names == ("A", "Y", "VSS", "VDD")
    assert (target_lib / "inv_mts" / "symbol" / "shape.marker").read_text() == "oa"
    source_env = json.loads((tmp_path / "run" / "transfer" / "oa" / "source-env.json").read_text())
    target_env = json.loads((tmp_path / "run" / "transfer" / "oa" / "target-env.json").read_text())
    assert source_env["CDS_LIB"].endswith("source-overlay.cds.lib")
    assert target_env["CDS_LIB"].endswith("target-overlay.cds.lib")
    assert str(source_cds.resolve()) not in "\n".join(f"{k}={v}" for k, v in target_env.items())
    assert str(session.target_cds_lib.resolve()) not in "\n".join(f"{k}={v}" for k, v in source_env.items())
    assert "MTS_TARGET_CDSLIB" not in source_env
    assert "MTS_SOURCE_LIB" not in target_env
    assert not any(name.startswith("CDS_MPS_") for name in source_env)
    assert not any(name.startswith("CDS_MPS_") for name in target_env)


def test_transfer_symbol_keeps_selected_project_environment_source_only(
    tmp_path: Path,
) -> None:
    _source_cds, _target_cds, _target_lib, session, request = _setup(tmp_path)
    dbaccess = _fake_dbaccess(tmp_path / "dbAccess")
    result = transfer_symbol(
        request,
        session,
        run_dir=tmp_path / "run",
        dbaccess=str(dbaccess),
        source_environment={
            **os.environ,
            "MTS_PROJECT_ONLY": "proj2",
            "CDS_MPS_SESSION": "must-not-start",
        },
    )

    source_env = json.loads(
        (tmp_path / "run" / "transfer" / "oa" / "source-env.json").read_text()
    )
    target_env = json.loads(
        (tmp_path / "run" / "transfer" / "oa" / "target-env.json").read_text()
    )
    assert result.source_process.pid != result.target_process.pid
    assert source_env["MTS_PROJECT_ONLY"] == "proj2"
    assert "MTS_PROJECT_ONLY" not in target_env
    assert not any(name.startswith("CDS_MPS_") for name in source_env)
    assert not any(name.startswith("CDS_MPS_") for name in target_env)


def test_transfer_symbol_rejects_existing_target_before_dbaccess(tmp_path: Path) -> None:
    source_cds, _target_cds, target_lib, session, request = _setup(tmp_path)
    view = target_lib / "inv_mts" / "symbol"
    view.mkdir(parents=True)
    (view / "keep").write_text("keep")
    dbaccess = _fake_dbaccess(tmp_path / "dbAccess")

    with pytest.raises(RequestValidationError, match="already exists"):
        transfer_symbol(request, session, run_dir=tmp_path / "run", dbaccess=str(dbaccess))
    assert (view / "keep").read_text() == "keep"
    assert not (tmp_path / "run").exists()


def test_transfer_symbol_overwrite_passes_authorization_and_reports_replacement(
    tmp_path: Path,
) -> None:
    source_cds, _target_cds, target_lib, session, request = _setup(tmp_path)
    request = NetlistRequest(
        request.source,
        request.dialect,
        request.models,
        request.process_options,
        request.simulator_options,
        TargetSelection(
            "target", "inv_mts", True, False, overwrite_symbol_view=True
        ),
    ).validate()
    view = target_lib / "inv_mts" / "symbol"
    view.mkdir(parents=True)
    (view / "old.marker").write_text("old", encoding="utf-8")
    dbaccess = _fake_dbaccess(tmp_path / "dbAccess")

    result = transfer_symbol(
        request,
        session,
        run_dir=tmp_path / "run",
        dbaccess=str(dbaccess),
    )

    assert result.target_existed_before is True
    assert result.overwrote_existing is True
    report = (tmp_path / "run" / "transfer" / "target-symbol.report").read_text(
        encoding="utf-8"
    )
    assert "overwrite=t" in report
    assert "target_existed=t" in report


def test_transfer_symbol_does_not_start_target_after_source_failure(tmp_path: Path) -> None:
    source_cds, _target_cds, target_lib, session, request = _setup(tmp_path)
    dbaccess = _fake_dbaccess(tmp_path / "dbAccess", fail_stage="source")

    with pytest.raises(SymbolTransferError, match="source symbol transfer failed"):
        transfer_symbol(request, session, run_dir=tmp_path / "run", dbaccess=str(dbaccess))
    root = tmp_path / "run" / "transfer" / "oa"
    assert (root / "source-env.json").is_file()
    assert not (root / "target-env.json").exists()
    assert not (target_lib / "inv_mts").exists()


def test_transfer_symbol_rejects_self_publication(tmp_path: Path) -> None:
    source_cds, target_cds, target_lib, _session_value, _request_value = _setup(tmp_path)
    # Bind the source name to the target session name to exercise the hard
    # self-publication guard without requiring a real OA library.
    request = NetlistRequest(
        SourceDesign(source_cds, "target", "inv", "schematic"),
        target=TargetSelection("target", "inv", generate_symbol_view=True),
    ).validate()
    session = SessionDescriptor(
        1,
        "test",
        target_cds,
        hashlib.sha256(target_cds.read_bytes()).hexdigest(),
        {"target": str(target_lib)},
    )
    dbaccess = _fake_dbaccess(tmp_path / "dbAccess")
    with pytest.raises(IsolationError, match="identical"):
        transfer_symbol(request, session, run_dir=tmp_path / "run", dbaccess=str(dbaccess))
