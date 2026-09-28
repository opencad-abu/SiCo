"""Import a staged netlist into the session-bound target text view."""

from __future__ import annotations

from pathlib import Path
from typing import Optional
from .artifacts import atomic_write_text, sha256_file
from .environment import SessionDescriptor, isolated_environment, write_cds_lib_overlay
from .errors import RequestValidationError, MtsNetlistorError
from .model_request import NetlistRequest
from .publication_result import PublicationResult
from .publication_netlist import rename_top_netlist, _ensure_spectre_language
from .publication_preflight import preflight_publication
from .publication_artifact import validate_run_artifact
try:
    from cadview.nl2view import ImportRequest, run_import, find_cds_lib_debug
except ImportError:  # pragma: no cover
    ImportRequest = None
    run_import = None
    find_cds_lib_debug = None


def publish_text_view(
    request: NetlistRequest,
    session: SessionDescriptor,
    *,
    netlist: str | Path,
    run_dir: str | Path,
    cds_text_to_5x: str = "cdsTextTo5x",
    cds_lib_debug: Optional[str] = None,
    strict_ownership: bool = False,
    timeout: float | None = None,
    cancel_event: object | None = None,
) -> PublicationResult:
    """Import a validated MTS netlist into the session-bound target library."""

    value = request.validate()
    validated_session = session.validate()
    if not value.target.generate_netlist_view:
        raise RequestValidationError("generate_netlist_view is not enabled")
    if value.corner_export.mode == "library":
        from .binding import publish_binding
        return publish_binding(value, validated_session, netlist=netlist, run_dir=run_dir,
                               timeout=timeout or 120.0, cancel_event=cancel_event,
                               strict_ownership=strict_ownership)
    if ImportRequest is None or run_import is None:
        raise MtsNetlistorError("cadview.nl2view is unavailable")
    preflight = preflight_publication(
        value,
        validated_session,
        netlist=netlist,
        cds_text_to_5x=cds_text_to_5x,
        include_symbol=False,
        include_text=True,
    )
    library = preflight.target_library
    cell = preflight.target_cell
    library_path = preflight.target_library_path
    source = preflight.netlist_path
    assert source is not None
    validate_run_artifact(
        value,
        source,
        Path(run_dir).expanduser().resolve(),
        strict_ownership=strict_ownership,
    )
    staging = Path(run_dir).expanduser().resolve() / "publish"
    staging.mkdir(parents=True, exist_ok=True)
    target_overlay = write_cds_lib_overlay(
        validated_session.target_cds_lib,
        staging / "target-overlay.cds.lib",
        forbidden_paths=(value.source.cds_lib,),
    )
    target_environment = isolated_environment(
        None,
        cds_lib=target_overlay,
        workdir=staging,
        forbidden_cds_lib=value.source.cds_lib,
    )
    publish_source = source
    if cell != value.source.cell:
        # IC23.10 cdsTextTo5x selects its analog parser from the file suffix
        # in practice, even when ``-LANG spectre`` is present.  Keep the
        # public/stable artifact as ``.spe`` but stage Spectre input as
        # ``.scs`` so the importer does not silently enter spice mode.
        import_suffix = ".scs" if value.dialect == "spectre" else value.output_suffix
        publish_source = staging / f"{cell}{import_suffix}"
        staged_text = rename_top_netlist(
            source.read_text(encoding="utf-8", errors="replace"),
            value.dialect,
            value.source.cell,
            cell,
        )
        if value.dialect == "spectre":
            staged_text = _ensure_spectre_language(staged_text)
        atomic_write_text(publish_source, staged_text)
    elif value.dialect == "spectre":
        # The stable source can retain the requested ``.spe`` naming, while
        # the importer receives a suffix that Cadence's parser actually
        # recognizes as Spectre.  Always stage a private copy, even when the
        # input already has a ".scs" suffix, so older/manual decks also get
        # the explicit language marker without mutating the stable artifact.
        publish_source = staging / f"{cell}.scs"
        staged_text = source.read_text(encoding="utf-8", errors="replace")
        atomic_write_text(publish_source, _ensure_spectre_language(staged_text))
    view = value.text_view
    view_directory = library_path / cell / view
    existed_before = view_directory.exists()
    if existed_before and not value.target.overwrite_netlist_view:
        raise RequestValidationError(f"target view already exists (overwrite policy is reject): {library}/{cell}/{view}")
    executable = preflight.text_executable
    assert executable is not None
    debug = find_cds_lib_debug(executable, cds_lib_debug, allow_path_lookup=True) if find_cds_lib_debug else cds_lib_debug
    log = staging / "nl2view.log"
    import_request = ImportRequest(
        source=publish_source,
        netlist_format="spectre" if value.dialect == "spectre" else "hspice",
        library=library,
        cell=cell,
        view=view,
        cds_library_file=target_overlay,
        log_file=log,
        copy_source=True,
        executable=executable,
        cds_lib_debug=debug,
        expected_library_path=library_path,
    )
    import_kwargs: dict[str, object] = {"environ": target_environment}
    # Preserve the small legacy call shape for direct/library callers.  The
    # GUI controller supplies these values, enabling the threaded cancellation
    # path only when it actually needs it.
    if timeout is not None:
        import_kwargs["timeout"] = timeout
    if cancel_event is not None:
        import_kwargs["cancel"] = cancel_event
    code = run_import(import_request, **import_kwargs)
    if code != 0:
        raise MtsNetlistorError(f"text view publication failed with exit code {code}")
    return PublicationResult(
        "succeeded",
        library,
        cell,
        view,
        publish_source,
        log,
        not existed_before,
        replaced_view=existed_before,
        target_overlay=target_overlay,
        message=f"target overlay sha256={sha256_file(target_overlay)}",
    )
