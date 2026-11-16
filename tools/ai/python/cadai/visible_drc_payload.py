"""Build the bounded response envelope for one visible-DRC job."""


def payload(job, receipt, *, schema):
    values = job.values
    export = values["export"]
    return {
        "ok": True,
        "schema": schema,
        "job_ref": values["run_ref"],
        "window_ref": values["window_ref"],
        "previous_job_ref": values.get("recheck_of") or None,
        "export": {
            "path": str(values["candidate"]),
            "size": export.get("size"),
            "sha256": export.get("sha256"),
            "visible_bbox": values["bbox"],
            "region_source": export.get("region_source", "visible"),
            "warnings": export.get("warnings", []),
            "counts": export.get("counts", {}),
            "connectivity_preserved": export.get(
                "connectivity_preserved", False
            ),
            "lvs_eligible": export.get("lvs_eligible", False),
        },
        "job": {
            "run_ref": values["run_ref"],
            "state": getattr(receipt, "state", ""),
            "scope": getattr(receipt, "scope", "candidate"),
            "top_cell": values["flow"].top_cell,
            "terminal": bool(getattr(receipt, "terminal", False)),
        },
    }


__all__ = ["payload"]
