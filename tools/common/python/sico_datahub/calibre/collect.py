"""Collect one completed Calibre flow into bounded DataHub outcomes."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Mapping, Optional

from ..codec import ArtifactRef, artifact_ref
from ..errors import HubError
from .drc_results import parse_drc_results
from .jobs import JobStore
from .lvs_results import parse_lvs_report
from .provenance import CalibreProvenance

_MAX_SVDB_ENTRIES = 5000


@dataclass(frozen=True)
class Collected:
    state: str
    message: str
    result: Optional[dict]
    artifacts: Mapping[str, ArtifactRef]


def _optional_artifact(path):
    if not path:
        return None
    target = Path(path)
    if not target.is_absolute() or not target.is_file():
        return None
    try:
        return artifact_ref(target)
    except HubError:
        return None


def _provenance(
    flow,
    run_ref,
    tool_version,
    started_at,
    finished_at,
    prepared=None,
    candidate_source=None,
):
    options = dict(flow.options)
    effective_deck = None
    effective_config = None
    if prepared is not None:
        options["deck_preparation"] = dict(prepared.preparation)
        effective_deck = prepared.effective_deck
        effective_config = prepared.config
    return CalibreProvenance(
        runset=flow.runset,
        dependencies_sha256=flow.runset_identity.dependencies_sha256,
        tool_version=tool_version,
        run_mode=flow.run_mode,
        top_cell=flow.top_cell,
        layer_map=flow.layer_map,
        candidate=_optional_artifact(flow.layout_path),
        source_layout=flow.source_layout,
        effective_deck=effective_deck,
        effective_config=effective_config,
        source_netlist=candidate_source,
        options=options,
        started_at=started_at,
        finished_at=finished_at,
        run_ref=run_ref,
    )


def _svdb_rows(directory):
    rows = []
    truncated = False
    pending = [directory]
    while pending:
        current = pending.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError:
            continue
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if stat.S_ISDIR(info.st_mode):
                pending.append(Path(entry.path))
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if len(rows) >= _MAX_SVDB_ENTRIES:
                truncated = True
                break
            rows.append({
                "path": str(Path(entry.path).relative_to(directory)),
                "size": info.st_size,
                "mtime_ns": info.st_mtime_ns,
            })
        if truncated:
            break
    rows.sort(key=lambda row: row["path"])
    return rows, truncated


def _svdb_manifest(flow, store, run_ref):
    if not flow.svdb_dir:
        return None
    directory = Path(flow.svdb_dir)
    if not directory.is_dir():
        return None
    rows, truncated = _svdb_rows(directory)
    listing = json.dumps(
        rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    value = {
        "schema": "calibre.svdb-manifest.v1",
        "path": str(directory),
        "top_cell": flow.top_cell,
        "entries": rows,
        "truncated": truncated,
        "listing_sha256": hashlib.sha256(listing).hexdigest(),
    }
    return store.write_metadata(run_ref, "svdb-manifest", value)


def _drc_state(result, exit_code, timed_out):
    if timed_out:
        return "unknown", "Calibre DRC timed out"
    if exit_code != 0:
        return "failed", "Calibre DRC exited with code " + str(exit_code)
    return result.status, ""


def _lvs_state(result, exit_code, timed_out):
    if timed_out:
        return "unknown", "Calibre LVS timed out"
    if result is not None and result.status == "incorrect":
        return "incorrect", ""
    if exit_code != 0:
        return "failed", "Calibre LVS exited with code " + str(exit_code)
    if result is None:
        return "unknown", "Calibre LVS report is unavailable"
    return result.status, ""


def _result_scope(flow):
    configured = flow.options.get("scope")
    if configured in {"full", "region", "fragment", "candidate"}:
        return configured
    selected = flow.options.get("rule_select_groups") or flow.options.get(
        "rule_select_checks"
    )
    return "candidate" if selected else "full"


def collect_run(
    flow,
    *,
    store: JobStore,
    run_ref,
    exit_code,
    timed_out,
    tool_version,
    started_at,
    finished_at,
    prepared=None,
    candidate_source=None,
) -> Collected:
    provenance = _provenance(
        flow,
        run_ref,
        tool_version,
        started_at,
        finished_at,
        prepared,
        candidate_source,
    )
    artifacts = {}
    result = None
    try:
        if flow.kind == "drc":
            summary_path = flow.summary if flow.summary.is_file() else None
            result = parse_drc_results(
                flow.results,
                summary_path=summary_path,
                provenance=provenance,
                scope=_result_scope(flow),
            )
        else:
            try:
                svdb = _svdb_manifest(flow, store, run_ref)
            except HubError:
                svdb = None
            report_path = flow.report if flow.report.is_file() else None
            callvs_log = flow.log_dir / "callvs.log"
            log_path = callvs_log if callvs_log.is_file() else None
            if report_path is not None:
                result = parse_lvs_report(
                    report_path,
                    log_path=log_path,
                    svdb_path=None,
                    provenance=provenance,
                    scope=_result_scope(flow),
                )
                if svdb is not None:
                    result = replace(result, svdb=svdb)
            if svdb is not None:
                artifacts["svdb"] = svdb
        if result is not None and flow.kind == "drc":
            if result.results is not None:
                artifacts["results"] = result.results
            if result.summary is not None:
                artifacts["summary"] = result.summary
        elif result is not None:
            if result.report is not None:
                artifacts["report"] = result.report
            if result.svdb is not None:
                artifacts["svdb"] = result.svdb
            if result.log is not None:
                artifacts["log"] = result.log
    except HubError as exc:
        state = "unknown" if timed_out else "failed"
        if timed_out:
            message = "Calibre " + flow.kind.upper() + " timed out"
        else:
            message = exc.message
        return Collected(state, message, None, artifacts)
    if flow.kind == "drc":
        state, message = _drc_state(result, exit_code, timed_out)
    else:
        state, message = _lvs_state(result, exit_code, timed_out)
    if result is not None and state in {"failed", "unknown"}:
        if result.status != state:
            result = replace(result, status=state)
    return Collected(
        state=state,
        message=message,
        result=result.to_dict() if result is not None else None,
        artifacts=artifacts,
    )


__all__ = ["Collected", "collect_run"]
