"""controller fixtures inputs."""

from __future__ import annotations
from pathlib import Path
import time
from mtsnetlistor.environment import SessionDescriptor
from mtsnetlistor.gui.controller import MtsController
from mtsnetlistor.model import NetlistRequest, SourceDesign, TargetSelection


def _wait(controller: MtsController, predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(controller.state):
            return
        time.sleep(0.01)
    raise AssertionError(f"controller did not reach expected state: {controller.state}")


def _publication_fixture(tmp_path: Path):
    source_cds = tmp_path / "source.cds.lib"
    source_cds.write_text("DEFINE source ./source\n")
    target_cds = tmp_path / "target.cds.lib"
    target_cds.write_text("DEFINE target ./target\n")
    target = tmp_path / "target"
    target.mkdir()
    session = SessionDescriptor(
        owner_pid=1,
        owner_start_time="test",
        target_cds_lib=target_cds,
        target_cds_lib_digest=__import__("hashlib").sha256(target_cds.read_bytes()).hexdigest(),
        target_library_paths={"target": str(target)},
    )
    items = []
    for cell in ("one", "two"):
        netlist = tmp_path / f"{cell}.spe"
        netlist.write_text(f"subckt {cell}\nends {cell}\n")
        run = tmp_path / f"run-{cell}"
        run.mkdir()
        request = NetlistRequest(
            SourceDesign(source_cds, "source", cell),
            target=TargetSelection(
                "target", cell, generate_netlist_view=True
            ),
        ).validate()
        items.append((request, netlist, run))
    return session, tuple(items)
