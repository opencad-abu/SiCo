"""Adapt a revalidated owned Symbol to generic binding and geometry component rows."""

from .circuit_geometry_schema import INSTANCE as GEOMETRY_INSTANCE
from .circuit_spec_schema import MASTER, digest, validate


def binding_result(native, args):
    revision = digest({k: native[k] for k in ("target", "library_path", "signature")})
    target = dict(zip(("library", "cell", "view"), native["target"]))
    master = dict(
        id=args["master_id"],
        target=target,
        library_path=native["library_path"],
        revision=revision,
        kind="design",
        terminals=[dict(name=p[0], direction=p[1]) for p in native["ports"]],
        terminals_complete=True,
        parameters=[],
        parameters_complete=True,
        callbacks=dict(status="none"),
    )
    geometry = dict(
        instance=args["instance_id"],
        master_revision=revision,
        parameters_digest=digest({}),
        occupied_bbox=native["occupied_bbox"],
        terminals=[
            dict(name=p[0], anchors=[dict(id="pin0", xy=p[2], escape=p[3])])
            for p in native["ports"]
        ],
    )
    validate(master, MASTER, "symbol binding")
    validate(geometry, GEOMETRY_INSTANCE, "symbol geometry")
    return dict(
        ok=True,
        stage="symbol_binding",
        symbol_ref=native["symbol_ref"],
        task_ref=native["task_ref"],
        template_ref=native["template_ref"],
        preview_digest=native["preview_digest"],
        master=master,
        geometry=geometry,
        source_ref=native["symbol_ref"],
        evidence_kind="session_capture",
        qualification="retained_symbol_revalidated; prepare_circuit_creation_still_required",
        parameter_policy="empty_parameters_only",
        connection_policy="explicit_pins",
    )
