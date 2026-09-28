"""Coordinate the symbol/text publication bundle and retain partial-failure evidence.

Legacy public imports reference the sole result/preflight/artifact/text owners.
Remove them after supported GUI, CLI and binding consumers have migrated."""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Mapping
from .environment import SessionDescriptor
from .errors import RequestValidationError
from .model_request import NetlistRequest
from .symbol import SymbolTransferResult, transfer_symbol
from .publication_result import PublicationResult, PublicationPreflight, PublicationBundleResult
from .publication_netlist import rename_top_netlist
from .publication_preflight import preflight_publication
from .publication_artifact import validate_run_artifact
from .publication_text import publish_text_view
from .publication_diagnostics import _publication_diagnostics


def publish_bundle(
    request: NetlistRequest,
    session: SessionDescriptor,
    *,
    netlist: str | Path,
    run_dir: str | Path,
    cds_text_to_5x: str = "cdsTextTo5x",
    cds_lib_debug: Optional[str] = None,
    dbaccess: str = "dbAccess",
    timeout: float = 120.0,
    cancel_event: object | None = None,
    strict_ownership: bool = False,
    source_environment: Optional[Mapping[str, str]] = None,
) -> PublicationBundleResult:
    """Publish selected symbol/text views with a single immutable preflight.

    Publication defaults to reject-overwrite and permits an explicit,
    per-view overwrite. If a failure occurs before any successful OA
    stage, the function reports ``generated_publish_failed``. If an earlier
    stage succeeded, or the failing stage may have left a partial target view,
    it reports ``manual_cleanup_required`` with exact paths. No rollback is
    claimed until a target-side deletion journal has been qualified.
    """

    value = request.validate()
    if not value.target.generate_symbol_view and not value.target.generate_netlist_view:
        raise RequestValidationError("no publication view is enabled")
    preflight = preflight_publication(
        value,
        session,
        netlist=netlist,
        cds_text_to_5x=cds_text_to_5x,
    )
    root = Path(run_dir).expanduser().resolve()
    if strict_ownership and value.target.generate_netlist_view:
        validate_run_artifact(
            value,
            Path(netlist).expanduser().resolve(),
            root,
            strict_ownership=True,
        )
    symbol_result: SymbolTransferResult | None = None
    text_result: PublicationResult | None = None
    active_view: Path | None = None
    try:
        # Symbol first leaves the source/target OA transfer boundary explicit;
        # text publication is then independently validated by nl2view.
        if value.target.generate_symbol_view:
            active_view = preflight.symbol_view_path
            symbol_result = transfer_symbol(
                value,
                session,
                run_dir=root,
                dbaccess=dbaccess,
                timeout=timeout,
                cancel_event=cancel_event,  # type: ignore[arg-type]
                source_environment=source_environment,
            )
            active_view = None
        if value.target.generate_netlist_view:
            active_view = preflight.text_view_path
            text_kwargs: dict[str, object] = {
                "netlist": netlist,
                "run_dir": root,
                "cds_text_to_5x": cds_text_to_5x,
                "cds_lib_debug": cds_lib_debug,
            }
            if strict_ownership:
                text_kwargs["strict_ownership"] = True
            text_kwargs["timeout"] = timeout
            text_kwargs["cancel_event"] = cancel_event
            if value.corner_export.mode == "library":
                from .binding import publish_binding
                text_result = publish_binding(value, session, netlist=netlist, run_dir=root,
                                              timeout=timeout, cancel_event=cancel_event,
                                              strict_ownership=strict_ownership)
            else:
                text_result = publish_text_view(value, session, **text_kwargs)
            active_view = None
    except Exception as exc:
        affected: list[str] = []
        if symbol_result is not None:
            assert preflight.symbol_view_path is not None
            affected.append(str(preflight.symbol_view_path))
        if text_result is not None:
            assert preflight.text_view_path is not None
            affected.append(str(preflight.text_view_path))
        # Importers and dbAccess may mutate OA state before their wrapper can
        # observe a filesystem object or return a success result. Once a
        # mutating stage starts, its destination is affected until a target-
        # side rollback journal proves otherwise.
        if active_view is not None:
            affected.append(str(active_view))
        if value.corner_export.mode == "library" and active_view == preflight.text_view_path:
            affected.append(str(preflight.target_library_path / preflight.target_cell / "data.dm"))
        affected = list(dict.fromkeys(affected))
        status = "manual_cleanup_required" if affected else "generated_publish_failed"
        message = str(exc)
        if affected:
            message += "; automatic OA rollback is not qualified; inspect only the listed target view(s)"
        diagnostics, log_files, cleanup = _publication_diagnostics(
            root=root,
            target_library=preflight.target_library,
            target_cell=preflight.target_cell,
            include_symbol=value.target.generate_symbol_view,
            include_text=value.target.generate_netlist_view,
            preflight=preflight,
            request=value,
            active_view=active_view,
            symbol_result=symbol_result,
            text_result=text_result,
            failure=exc,
        )
        return PublicationBundleResult(
            status=status,
            target_library=preflight.target_library,
            target_cell=preflight.target_cell,
            text=text_result,
            symbol=symbol_result,
            affected_views=tuple(affected),
            rollback=(),
            message=message,
            diagnostics=diagnostics,
            log_files=log_files,
            cleanup_instructions=cleanup,
        )
    diagnostics, log_files, _cleanup = _publication_diagnostics(
        root=root,
        target_library=preflight.target_library,
        target_cell=preflight.target_cell,
        include_symbol=value.target.generate_symbol_view,
        include_text=value.target.generate_netlist_view,
        preflight=preflight,
        request=value,
        symbol_result=symbol_result,
        text_result=text_result,
    )
    return PublicationBundleResult(
        status="succeeded",
        target_library=preflight.target_library,
        target_cell=preflight.target_cell,
        text=text_result,
        symbol=symbol_result,
        message=(text_result.message if value.corner_export.mode == "library" and text_result else "publication completed"),
        diagnostics=diagnostics,
        log_files=log_files,
    )


__all__ = ["PublicationResult", "PublicationPreflight", "PublicationBundleResult", "rename_top_netlist", "preflight_publication", "validate_run_artifact", "publish_text_view", "publish_bundle"]
