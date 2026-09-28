"""Describe publication evidence and scoped manual cleanup requirements."""

from __future__ import annotations

from pathlib import Path
from .model_request import NetlistRequest
from .publication_result import PublicationResult, PublicationPreflight
from .symbol import SymbolTransferResult


def _publication_log_paths(
    root: Path,
    *,
    include_symbol: bool,
    include_text: bool,
) -> tuple[Path, ...]:
    """Return deterministic evidence paths for every requested publisher stage.

    The paths are returned even when a stage failed before creating its log.
    A missing path is useful diagnostic information: it tells the operator
    that the worker did not reach the point where that report/log is written.
    """

    paths: list[Path] = []
    if include_symbol:
        paths.extend(
            (
                root / "logs" / "source-symbol-worker.log",
                root / "logs" / "target-symbol-worker.log",
                root / "transfer" / "source-symbol.report",
                root / "transfer" / "target-symbol.report",
            )
        )
    if include_text:
        paths.append(root / "publish" / "nl2view.log")

    return tuple(paths)


def _publication_diagnostics(
    *,
    root: Path,
    target_library: str,
    target_cell: str,
    include_symbol: bool,
    include_text: bool,
    preflight: PublicationPreflight,
    request: NetlistRequest,
    active_view: Path | None = None,
    symbol_result: SymbolTransferResult | None = None,
    text_result: PublicationResult | None = None,
    failure: BaseException | None = None,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Build stable, human-readable publication evidence for CLI and GUI.

    This deliberately reports paths and stage boundaries only.  It does not
    attempt OA inspection or deletion from Python, because doing so would
    violate the source/target process isolation contract.
    """

    log_paths = _publication_log_paths(
        root,
        include_symbol=include_symbol,
        include_text=include_text,
    )
    if request.corner_export.mode == "library" and include_text:
        log_paths = tuple(p for p in log_paths if p.name != "nl2view.log") + (
            root / "publish" / "model-binding.log", root / "publish" / "model-binding.report")
    requested: list[str] = []
    if include_symbol:
        requested.append("symbol")
    if include_text:
        requested.append("text")
    diagnostics: list[str] = [
        f"run_dir={root}",
        f"target={target_library}/{target_cell}",
        f"requested_views={','.join(requested)}",
    ]
    if include_symbol:
        diagnostics.extend(
            (
                "symbol_overwrite_requested="
                + str(request.target.overwrite_symbol_view).lower(),
                "symbol_destination_before="
                + ("existing" if preflight.symbol_existed_before else "absent"),
            )
        )
    if include_text:
        diagnostics.extend(
            (
                "text_overwrite_requested="
                + str(request.target.overwrite_netlist_view).lower(),
                "text_destination_before="
                + ("existing" if preflight.text_existed_before else "absent"),
            )
        )
    if symbol_result is not None:
        diagnostics.append("symbol_stage=succeeded")
        diagnostics.append(f"symbol_target_view={target_library}/{target_cell}/symbol")
        diagnostics.append(
            "symbol_action="
            + (
                "replaced"
                if getattr(
                    symbol_result,
                    "overwrote_existing",
                    preflight.symbol_existed_before,
                )
                else "created"
            )
        )
    if text_result is not None:
        diagnostics.append("text_stage=succeeded")
        diagnostics.append(f"text_target_view={target_library}/{target_cell}/{text_result.target_view}")
        diagnostics.append(
            "text_action=" + ("replaced" if text_result.replaced_view else "created")
        )
    if active_view is not None:
        diagnostics.append(f"active_stage_target_view={active_view}")
    if failure is not None:
        diagnostics.extend(
            (
                f"failure_type={type(failure).__name__}",
                f"failure={failure}",
            )
        )
    for path in log_paths:
        diagnostics.append(f"evidence_log={'present' if path.is_file() else 'missing'}:{path}")

    cleanup: list[str] = []
    if failure is not None and (active_view is not None or symbol_result is not None or text_result is not None):
        cleanup.extend(
            (
                "automatic OA rollback is not qualified; do not assume the target is unchanged",
                "inspect only the listed target view(s) in the target Virtuoso/Library Manager session",
            )
        )
        affected_existing = (
            (preflight.symbol_existed_before and (
                symbol_result is not None or active_view == preflight.symbol_view_path
            ))
            or (preflight.text_existed_before and (
                text_result is not None or active_view == preflight.text_view_path
            ))
        )
        affected_new = (
            (not preflight.symbol_existed_before and (
                symbol_result is not None or active_view == preflight.symbol_view_path
            ))
            or (not preflight.text_existed_before and (
                text_result is not None or active_view == preflight.text_view_path
            ))
        )
        if affected_existing:
            cleanup.append(
                "do not delete a destination that existed before this run; its old OA contents may have been replaced; restore only from a qualified OA backup or version control"
            )
        if affected_new:
            cleanup.append(
                "remove a newly-created partial target view only after confirming it belongs to this publication run, then refresh the target library"
            )
    return tuple(diagnostics), tuple(str(path) for path in log_paths), tuple(cleanup)
