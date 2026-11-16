"""Restore one visible-DRC job from its durable workspace locator."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from .visible_drc_job import Job


class RestoreError(RuntimeError):
    """The locator or the underlying Calibre receipt cannot be used."""


def restore_job(registry, runner_factory, job_ref):
    if registry is None:
        raise RestoreError("Visible DRC job is unknown")
    try:
        record = registry.load(job_ref)
    except Exception as exc:
        raise RestoreError("Visible DRC job registry is unavailable") from exc
    if record is None:
        raise RestoreError("Visible DRC job is unknown")
    try:
        runner = runner_factory(
            record["work_root"],
            record["calibre_root"],
            record["timeout_seconds"],
        )
        receipt = runner.status(job_ref)
    except Exception as exc:
        if "runner" in locals():
            runner.close()
        raise RestoreError("Visible DRC job receipt is unavailable") from exc
    if getattr(receipt, "terminal", False):
        try:
            registry.update_state(job_ref, receipt.state)
            registry.append_event(
                job_ref, receipt.state, getattr(receipt, "message", "restored")
            )
        except Exception:
            pass
    values = {
        "run_ref": record["job_ref"],
        "window_ref": record["window_ref"],
        "config_path": Path(record["config_path"]),
        "work_root": Path(record["work_root"]),
        "calibre_root": Path(record["calibre_root"]),
        "timeout": record["timeout_seconds"],
        "flow": SimpleNamespace(top_cell=record["top_cell"]),
        "candidate": Path(record["candidate"]),
        "bbox": record["bbox"],
        "recheck_of": record.get("recheck_of", ""),
        "config_sha256": record.get("config_sha256", ""),
        "runset_dependencies_sha256": record.get(
            "runset_dependencies_sha256", ""
        ),
        "export": {
            "size": record["size"],
            "sha256": record["sha256"],
            "warnings": record["warnings"],
            "counts": record["counts"],
            "connectivity_preserved": False,
            "lvs_eligible": False,
            "region_source": record.get("region_source", "visible"),
        },
    }
    return Job(values=values, runner=runner, restored=True)


__all__ = ["RestoreError", "restore_job"]
