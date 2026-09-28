"""Threaded, Qt-free orchestration used by CLI and the optional GUI."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from ..catalog import load_source_catalog, load_target_catalog
from ..defaults import DefaultsProbeRequest, MaeSetup
from ..errors import MtsNetlistorError
from ..model import NetlistRequest, SourceDesign
from ..publish import (
    PublicationBundleResult,
    preflight_publication,
    publish_bundle,
    validate_run_artifact,
)
from ..workflow import (
    generate,
    generate_many,
    probe_defaults,
    read_source_defaults as workflow_read_source_defaults,
)

from .controller_tasks import CATALOG_LOG_PREFIX, ControllerState, ControllerTasks


class MtsController(ControllerTasks):
    """Latest-request-only background controller.

    The controller has no Qt dependency.  A Qt adapter can subscribe to
    ``on_state`` and marshal callbacks onto the GUI thread; tests and headless
    integrations can use the same class directly.
    """

    def refresh_source_catalog(
        self,
        cds_library_file: str | Path,
        *,
        environment: Optional[dict[str, str]] = None,
        dbaccess: str | None = "dbAccess",
        allow_fallback: bool = False,
        timeout: float = 30.0,
        cache_ttl: float | None = None,
        force_refresh: bool = False,
    ) -> int:
        token, cancel = self._begin("cataloging_source")
        # Source catalog workers must never be allowed to inspect the target
        # Virtuoso domain.  Capture the session boundary before submitting the
        # task so a later session replacement cannot change this operation's
        # isolation contract.
        session = self.session
        forbidden_target_cds_lib = (
            None if session is None else session.target_cds_lib
        )
        catalog_kwargs = {
            "dbaccess": dbaccess,
            "allow_fallback": allow_fallback,
            "timeout": timeout,
            "cancel_event": cancel,
            "cache_ttl": cache_ttl,
            "force_refresh": force_refresh,
            "output_callback": lambda message: self._emit_log(
                token, CATALOG_LOG_PREFIX + message
            ),
        }
        if forbidden_target_cds_lib is not None:
            catalog_kwargs["forbidden_target_cds_lib"] = forbidden_target_cds_lib
        return self._submit(
            token,
            cancel,
            lambda: self._source_call(load_source_catalog, cds_library_file, environment,
                environment=environment,
                **catalog_kwargs,
            ),
            lambda result: self._finish(token, stage="source_catalog_ready", source_catalog=result),
            "cataloging_source",
        )

    def refresh_target_catalog(
        self,
        *,
        dbaccess: str | None = "dbAccess",
        timeout: float = 30.0,
    ) -> int:
        if self.session is None:
            raise MtsNetlistorError("target session is not configured")
        token, cancel = self._begin("cataloging_target")
        session = self.session
        return self._submit(
            token,
            cancel,
            lambda: load_target_catalog(session, dbaccess=dbaccess, timeout=timeout, cancel_event=cancel),
            lambda result: self._finish(token, stage="target_catalog_ready", target_catalog=result),
            "cataloging_target",
        )

    def generate(
        self,
        request: NetlistRequest,
        *,
        environ: Optional[dict[str, str]] = None,
        ocean: str | None = None,
        timeout: float = 600.0,
    ) -> int:
        token, cancel = self._begin("generating")
        return self._submit(
            token,
            cancel,
            lambda: self._source_call(generate, request, environ,
                environ=environ,
                ocean=ocean,
                timeout=timeout,
                cancel_event=cancel,
                output_callback=lambda message: self._emit_log(token, message),
            ),
            lambda result: self._finish(token, stage="generation_ready", generation=result),
            "generating",
        )

    def probe_defaults(
        self,
        request: DefaultsProbeRequest | NetlistRequest,
        *,
        environ: Optional[dict[str, str]] = None,
        ocean: str | None = None,
        timeout: float = 180.0,
    ) -> int:
        """Probe PDK runtime defaults without touching the target session."""
        token, cancel = self._begin("probing_defaults")
        return self._submit(
            token,
            cancel,
            lambda: self._source_call(probe_defaults, request, environ,
                environ=environ,
                ocean=ocean,
                timeout=timeout,
                cancel_event=cancel,
                output_callback=lambda message: self._emit_log(token, message),
            ),
            lambda result: self._finish(token, stage="defaults_ready", defaults=result),
            "probing_defaults",
        )

    # Descriptive alias used by the GUI and external integrations.  The
    # report-level method remains available for callers that need raw ASI
    # snapshots and diagnostics.
    def read_source_defaults(
        self,
        source: SourceDesign,
        *,
        dialect: str = "spectre",
        environ: Optional[dict[str, str]] = None,
        ocean: str | None = None,
        timeout: float = 180.0,
        provider: str = "asi_initialization",
        mae_setup: MaeSetup | None = None,
    ) -> int:
        token, cancel = self._begin("probing_defaults")
        return self._submit(
            token,
            cancel,
            lambda: self._source_call(workflow_read_source_defaults, source, environ,
                dialect=dialect,
                environ=environ,
                ocean=ocean,
                timeout=timeout,
                cancel_event=cancel,
                output_callback=lambda message: self._emit_log(token, message),
                provider=provider,
                mae_setup=mae_setup,
            ),
            lambda result: self._finish(
                token, stage="defaults_ready", defaults=result
            ),
            "probing_defaults",
        )

    def generate_many(
        self,
        request: NetlistRequest,
        *,
        environ: Optional[dict[str, str]] = None,
        ocean: str | None = None,
        timeout: float = 600.0,
    ) -> int:
        """Generate selected cells sequentially with separate design sessions."""

        token, cancel = self._begin("generating")
        return self._submit(
            token,
            cancel,
            lambda: self._source_call(generate_many, request, environ,
                environ=environ,
                ocean=ocean,
                timeout=timeout,
                cancel_event=cancel,
                output_callback=lambda message: self._emit_log(token, message),
            ),
            lambda result: self._finish(token, stage="generation_ready", generation=result),
            "generating",
        )

    def publish(
        self,
        request: NetlistRequest,
        *,
        netlist: str | Path,
        run_dir: str | Path,
        cds_text_to_5x: str = "cdsTextTo5x",
        cds_lib_debug: str | None = None,
        dbaccess: str = "dbAccess",
        timeout: float = 120.0,
        source_environment: Optional[dict[str, str]] = None,
    ) -> int:
        if self.session is None:
            raise MtsNetlistorError("target session is not configured")
        token, cancel = self._begin("publishing")
        session = self.session
        return self._submit(
            token,
            cancel,
            lambda: publish_bundle(
                request,
                session,
                netlist=netlist,
                run_dir=run_dir,
                cds_text_to_5x=cds_text_to_5x,
                cds_lib_debug=cds_lib_debug,
                dbaccess=dbaccess,
                timeout=timeout,
                cancel_event=cancel,
                strict_ownership=True,
                source_environment=source_environment,
            ),
            lambda result: self._finish(token, stage="publication_ready", publication=result),
            "publishing",
        )

    def publish_many(
        self,
        items: tuple[tuple[NetlistRequest, str | Path, str | Path], ...],
        *,
        cds_text_to_5x: str = "cdsTextTo5x",
        cds_lib_debug: str | None = None,
        dbaccess: str = "dbAccess",
        timeout: float = 120.0,
        source_environment: Optional[dict[str, str]] = None,
    ) -> int:
        """Publish each generated cell result in deterministic order."""

        if self.session is None:
            raise MtsNetlistorError("target session is not configured")
        token, cancel = self._begin("publishing")
        session = self.session

        def worker() -> tuple[PublicationBundleResult, ...]:
            # Resolve every destination before the first OA mutation.  This
            # preserves the batch-level reject-overwrite guarantee instead of
            # discovering a later cell conflict after earlier views changed.
            destinations: set[tuple[str, str, str]] = set()
            prepared: list[tuple[NetlistRequest, Path, Path]] = []
            for request, netlist, _run_dir in items:
                validated = request.validate()
                source = Path(netlist).expanduser().resolve()
                run_dir = Path(_run_dir).expanduser().resolve()
                preflight = preflight_publication(
                    validated,
                    session,
                    netlist=source,
                    cds_text_to_5x=cds_text_to_5x,
                )
                validate_run_artifact(
                    validated,
                    source,
                    run_dir,
                    strict_ownership=True,
                )
                requested = []
                if validated.target.generate_symbol_view:
                    requested.append("symbol")
                if validated.target.generate_netlist_view:
                    requested.append(validated.text_view)
                for view in requested:
                    destination = (
                        preflight.target_library,
                        preflight.target_cell,
                        view,
                    )
                    if destination in destinations:
                        raise MtsNetlistorError(
                            "duplicate batch publication destination: "
                            + "/".join(destination)
                        )
                    destinations.add(destination)
                prepared.append((validated, source, run_dir))
            results: list[PublicationBundleResult] = []
            for request, netlist, run_dir in prepared:
                if cancel.is_set():
                    # Return completed results so a cancellation after an OA
                    # mutation does not erase the evidence for earlier cells.
                    if results:
                        return tuple(results)
                    raise MtsNetlistorError("publication canceled")
                result = publish_bundle(
                    request,
                    session,
                    netlist=netlist,
                    run_dir=run_dir,
                    cds_text_to_5x=cds_text_to_5x,
                    cds_lib_debug=cds_lib_debug,
                    dbaccess=dbaccess,
                    timeout=timeout,
                    cancel_event=cancel,
                    strict_ownership=True,
                    source_environment=source_environment,
                )
                results.append(result)
                # A non-success bundle can mean OA mutation already happened
                # and manual cleanup is required.  Never widen that state by
                # starting a later cell's publisher.
                if result.status != "succeeded":
                    break
            return tuple(results)

        return self._submit(
            token,
            cancel,
            worker,
            lambda result: self._finish(token, stage="publication_ready", publication=result),
            "publishing",
        )


__all__ = ["ControllerState", "MtsController"]
