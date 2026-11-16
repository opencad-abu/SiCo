"""Reuse the existing LVS stage CLI to export candidate GDS and CDL."""

from __future__ import annotations

from pathlib import Path

from ..codec import artifact_ref
from ..errors import HubError
from .jobs import JobStore
from .process import run_process


def export_layout(
    flow,
    *,
    store: JobStore,
    export_ref,
    lvs_command,
    environment,
    tool_version="",
    timeout=180.0,
):
    """Run the existing LVS cdl+gds stages and return their artifact refs."""
    if type(timeout) not in {int, float} or not 0 < timeout <= 1800:
        raise HubError("invalid_arguments", "Invalid Calibre export timeout")
    try:
        Path(flow.run_dir).mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise HubError("read_failed", "Calibre export directory is unavailable") from exc
    stdout_path = store.export_log_path(export_ref, "out")
    stderr_path = store.export_log_path(export_ref, "err")
    outcome = run_process(
        [
            str(lvs_command),
            "run",
            str(flow.config.path),
            "--stop-after",
            "gds",
        ],
        cwd=flow.run_dir,
        env=environment,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        timeout=timeout,
    )
    if outcome.timed_out:
        raise HubError("timeout", "Calibre layout export timed out")
    if outcome.exit_code != 0:
        raise HubError(
            "read_failed",
            "Calibre layout export failed with code " + str(outcome.exit_code),
        )
    gds = Path(flow.run_dir) / "db/gds" / (flow.layout_cell + ".gds")
    cdl = Path(flow.run_dir) / "db/cdl" / (flow.source_cell + ".cdl")
    if not gds.is_file() or not cdl.is_file():
        raise HubError("source_not_captured", "Calibre layout export is incomplete")
    return {
        "export_ref": export_ref,
        "config": flow.config.to_dict(),
        "runset": flow.runset.to_dict(),
        "top_cell": flow.top_cell,
        "layout_cell": flow.layout_cell,
        "source_cell": flow.source_cell,
        "gds": artifact_ref(gds).to_dict(),
        "cdl": artifact_ref(cdl).to_dict(),
        "stdout": artifact_ref(stdout_path).to_dict(),
        "stderr": artifact_ref(stderr_path).to_dict(),
        "tool_version": tool_version,
        "started_at": outcome.started_at,
        "finished_at": outcome.finished_at,
    }


__all__ = ["export_layout"]
