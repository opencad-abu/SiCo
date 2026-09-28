"""publication fixtures inputs."""

from __future__ import annotations
import hashlib
import json
from pathlib import Path
from mtsnetlistor.environment import SessionDescriptor
from mtsnetlistor.model import NetlistRequest, SourceDesign, TargetSelection


def _request(
    source_cds_lib: Path,
    source_cell: str = "bufferx1",
    *,
    target_cell: str | None = "renamed",
    target_library: str | None = "target",
) -> NetlistRequest:
    return NetlistRequest(
        source=SourceDesign(source_cds_lib, "source", source_cell, "schematic"),
        dialect="spectre",
        target=TargetSelection(
            target_library,
            target_cell,
            generate_netlist_view=True,
        ),
    ).validate()


def _session(tmp_path: Path, target_cds_lib: Path, target_library: Path) -> SessionDescriptor:
    return SessionDescriptor(
        owner_pid=1,
        owner_start_time="test",
        target_cds_lib=target_cds_lib,
        target_cds_lib_digest=hashlib.sha256(target_cds_lib.read_bytes()).hexdigest(),
        target_library_paths={"target": str(target_library)},
    )


def _setup(tmp_path: Path) -> tuple[Path, Path, Path, Path, SessionDescriptor, NetlistRequest]:
    source_cds_lib = tmp_path / "source.cds.lib"
    source_cds_lib.write_text("DEFINE source ./source-lib\n", encoding="utf-8")
    target_cds_lib = tmp_path / "target.cds.lib"
    target_cds_lib.write_text("DEFINE target ./target-lib\n", encoding="utf-8")
    source_library = tmp_path / "source-lib"
    target_library = tmp_path / "target-lib"
    source_library.mkdir()
    target_library.mkdir()
    session = _session(tmp_path, target_cds_lib, target_library)
    request = _request(source_cds_lib)
    netlist = tmp_path / "bufferx1.spe"
    netlist.write_text(
        "simulator lang=spectre\n"
        "subckt bufferx1 A VDD VSS Y\n"
        "  inv0 (A VDD VSS Y) inv\n"
        "ends bufferx1\n"
        "subckt inv A VDD VSS Y\n"
        "ends inv\n",
        encoding="utf-8",
    )
    return source_cds_lib, target_cds_lib, source_library, target_library, session, request


def _managed_run_with_successful_artifacts(
    tmp_path: Path, request: NetlistRequest, content: str
) -> tuple[Path, Path, Path]:
    """Create the same ownership evidence emitted by workflow.generate."""

    from mtsnetlistor.artifacts import sha256_file
    from mtsnetlistor.config import canonical_request_digest

    run = tmp_path / "managed" / ".mts-netlistor" / "runs" / "run-1"
    scoped = run / "scoped" / f"{request.source.cell}{request.output_suffix}"
    stable = run.parent.parent.parent / f"{request.source.cell}{request.output_suffix}"
    scoped.parent.mkdir(parents=True)
    stable.parent.mkdir(parents=True, exist_ok=True)
    scoped.write_text(content, encoding="utf-8")
    stable.write_text(content, encoding="utf-8")
    digest = canonical_request_digest(request)
    (run / ".mts-netlistor-run.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "request_digest": digest,
                "source_library": request.source.library,
                "source_cell": request.source.cell,
                "source_view": request.source.view,
            }
        ),
        encoding="utf-8",
    )
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "succeeded",
                "request_digest": digest,
                "dialect": request.dialect,
                "scoped_netlist": str(scoped),
                "scoped_sha256": sha256_file(scoped),
                "stable_output": str(stable),
                "stable_sha256": sha256_file(stable),
            }
        ),
        encoding="utf-8",
    )
    return run, scoped, stable
