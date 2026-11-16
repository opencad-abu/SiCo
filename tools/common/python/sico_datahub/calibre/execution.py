"""Select full-flow or candidate execution and prepare its private inputs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

from ..codec import ArtifactRef
from .candidate import prepare_candidate
from .context import FlowRequest
from .deck import PreparedFlow, prepare_flow
from .jobs import JobStore


@dataclass(frozen=True)
class ExecutionPlan:
    flow: FlowRequest
    prepared: PreparedFlow
    command: tuple
    cwd: Path
    candidate: Optional[ArtifactRef]
    source: Optional[ArtifactRef]
    scope: str


def prepare_execution(
    flow,
    *,
    store: JobStore,
    run_ref,
    candidate_path,
    source_path=None,
    region,
    calibre_executable,
    commands: Mapping[str, Path],
    work_root,
    timeout,
) -> ExecutionPlan:
    if candidate_path is None:
        prepared = prepare_flow(
            flow,
            store=store,
            run_ref=run_ref,
            calibre_executable=calibre_executable,
            timeout=timeout,
        )
        return ExecutionPlan(
            flow=flow,
            prepared=prepared,
            command=(str(commands[flow.kind]), "run", prepared.config.path),
            cwd=Path(work_root),
            candidate=None,
            source=None,
            scope="full",
        )
    spec = prepare_candidate(
        flow,
        store=store,
        run_ref=run_ref,
        candidate_path=candidate_path,
        source_path=source_path,
        region=region,
        calibre_executable=calibre_executable,
        timeout=timeout,
    )
    return ExecutionPlan(
        flow=spec.flow,
        prepared=spec.prepared,
        command=tuple(spec.command),
        cwd=spec.flow.run_dir,
        candidate=spec.candidate,
        source=spec.source,
        scope="candidate",
    )


__all__ = ["ExecutionPlan", "prepare_execution"]
