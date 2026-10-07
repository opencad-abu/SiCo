"""Offline capability discovery derived from the registered measurement contracts."""

from copy import deepcopy
from itertools import product

from .ac_response_schema import RESPONSE_SCHEMA, RESPONSE_TOOLS
from .ac_schema import AC_MEASURE_SCHEMA, AC_SCHEMA, AC_TOOLS
from .circuit_schema import string_schema, tool
from .circuit_spec_schema import CircuitSpecError, enum, obj, validate
from .measurement_failures import failures
from .measurement_recipe_fields import recipe_fields
from .measurement_semantics import semantics
from .result_contract import ResultError, digest
from .waveform_measure import UNITS
from .waveform_pair import MATCH_KEYS
from .waveform_schema import (
    MAX_SAMPLES,
    MEASUREMENT_SCHEMA,
    PAIR_SCHEMA,
    WAVE_SCHEMA,
    WAVEFORM_TOOLS,
)
from .waveform_spec_kinds import PAIRED_AC, RECIPE_SCHEMAS
from .waveform_spec_schema import SPEC_REPORT_SCHEMA

CATALOG_SCHEMA = "cad.measurement.capabilities.v1"
CAPABILITY_SCHEMA = "cad.measurement.capability.v1"
# Bump for meaning/applicability/failure changes; content hash also detects schema drift.
SEMANTICS_VERSION = 1
BINDINGS = {
    "single": (
        "measure_waveform",
        "read_maestro_waveform",
        "query_waveform",
        WAVE_SCHEMA,
        MEASUREMENT_SCHEMA,
    ),
    "pair": (
        "measure_waveform_pair",
        "read_maestro_waveform",
        "query_waveform",
        WAVE_SCHEMA,
        PAIR_SCHEMA,
    ),
    "ac_signal": (
        "measure_ac_waveform",
        "read_maestro_ac_waveform",
        "query_ac_waveform",
        AC_SCHEMA,
        AC_MEASURE_SCHEMA,
    ),
    "ac_transfer": (
        "measure_ac_transfer",
        "read_maestro_ac_waveform",
        "query_ac_waveform",
        AC_SCHEMA,
        AC_MEASURE_SCHEMA,
    ),
    "ac_response": (
        "measure_ac_response",
        "read_maestro_ac_waveform",
        "query_ac_waveform",
        AC_SCHEMA,
        RESPONSE_SCHEMA,
    ),
}
MEASUREMENT_TOOLS = {t["name"]: t for t in WAVEFORM_TOOLS + AC_TOOLS + RESPONSE_TOOLS}
CATALOG_TOOLS = [
    tool(
        "list_measurement_capabilities",
        "Discover implemented generic waveform measurement capabilities offline, "
        "without a workspace "
        "or Virtuoso. Optional exact kind/operation/tool_name filters and literal case-insensitive "
        "query over id/name/definition; all filters combine with AND. Results are stable id-sorted "
        "summaries with catalog_revision; use get_measurement_capability for "
        "authoritative schemas, "
        "units, applicability, explicit parameters and failure rules. No recipe "
        "defaults, simulation "
        "or project/PDK discovery. An empty result does not authorize inventing an operation.",
        {
            "kind": enum(*BINDINGS),
            "operation": string_schema(96),
            "tool_name": string_schema(96),
            "query": string_schema(128),
            "offset": {"type": "integer", "minimum": 0, "maximum": 4096},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        },
        (),
    ),
    tool(
        "get_measurement_capability",
        "Read one generic measurement capability by exact id, e.g. ac_response.phase_margin. "
        "Return the live tool input schema, operation-specific recipe schema, required quantities, "
        "analysis/type/unit constraints, mathematical semantics, failure patterns, limits and "
        "specification integration. Schema validation alone does not verify physical "
        "compatibility; "
        "also satisfy semantic rules. All task values remain explicit. Optional catalog_revision "
        "guards a prior list selection against changed catalog content. Offline and read-only; "
        "unknown ids or stale revisions are errors, never substitutions.",
        {
            "capability_id": string_schema(128),
            "catalog_revision": string_schema(64, pattern=r"^[0-9a-f]{64}$"),
        },
        ("capability_id",),
    ),
]
CATALOG_NAMES = frozenset(t["name"] for t in CATALOG_TOOLS)


def _quantity_paths(quantities):
    """Publish concrete field paths, not the compact grouping used by semantic notes."""
    return {
        ("" if path.startswith("tool.") else "recipe.") + ".".join(parts): rule
        for path, rule in quantities.items()
        for parts in product(*(part.split("/") for part in path.split(".")))
    }


def _entry(kind, operation):
    name, capture, query, wave_schema, report_schema = BINDINGS[kind]
    definition = MEASUREMENT_TOOLS[name]
    tool_schema = deepcopy(definition["inputSchema"])
    original = tool_schema["properties"]["recipes"]["items"]
    fields = recipe_fields(kind, operation)
    schema = obj(
        {k: deepcopy(v) for k, v in original["properties"].items() if k in fields}, sorted(fields)
    )
    schema["properties"]["operation"] = enum(operation)
    meaning = semantics(kind, operation)
    meaning["quantities"] = _quantity_paths(meaning["quantities"])
    return dict(
        schema=CAPABILITY_SCHEMA,
        capability_id=kind + "." + operation,
        semantics_version=SEMANTICS_VERSION,
        kind=kind,
        operation=operation,
        tool_name=name,
        tool_description=definition["description"],
        tool_input_schema=tool_schema,
        recipe_schema=schema,
        required_tool_arguments=tool_schema["required"],
        required_recipe_fields=schema["required"],
        schema_policy="Projection of live tool schema with shared execution field rules; "
        "semantic and physical-unit checks remain required",
        **meaning,
        capture=dict(
            tool_name=capture,
            query_tool=query,
            waveform_schema=wave_schema,
            prerequisites="An exact settled history/test/corner/point and explicitly "
            "selected signal; "
            "discover source units/types before constructing a recipe",
        ),
        execution=dict(
            mode="offline",
            simulation=False,
            writes_design=False,
            writes_measurement_artifact=True,
            report_schema=report_schema,
            row_statuses=["scalar", "missing", "error"],
            spec_qualified=None,
        ),
        specification=dict(
            tool_name="evaluate_waveform_specs",
            query_tool="query_waveform_spec_report",
            report_schema=SPEC_REPORT_SCHEMA,
            kind=kind,
            exact_recipe_and_source_match=True,
            top_level_denominator_floor_required=kind in PAIRED_AC,
            coverage_and_bounds="explicit_task_or_project_contract",
            angle_conversion="deg_rad_without_wrapping" if kind.startswith("ac_") else None,
            stability_qualified=None,
        ),
        pair_source_match_keys=list(MATCH_KEYS) if kind != "single" and kind != "ac_signal" else [],
        failures=failures(kind, operation),
        limits=dict(
            samples_per_waveform=MAX_SAMPLES,
            recipes_per_call=tool_schema["properties"]["recipes"]["maxItems"],
            occurrence_max=schema["properties"].get("occurrence", {}).get("maximum"),
            event_occurrence_max=schema["properties"]
            .get("input_event", {})
            .get("properties", {})
            .get("occurrence", {})
            .get("maximum"),
        ),
        defaults_policy="No circuit, PDK, signal, frequency, threshold, floor, "
        "reference or acceptance defaults",
    )


def build_catalog():
    entries = [
        _entry(kind, op)
        for kind, schema in RECIPE_SCHEMAS.items()
        for op in schema["properties"]["operation"]["enum"]
    ]
    entries.sort(key=lambda e: e["capability_id"])
    catalog = dict(
        schema=CATALOG_SCHEMA,
        semantics_version=SEMANTICS_VERSION,
        scope="implemented_real_waveform_and_complex_ac_scalar_measurements",
        unit_registry={
            u: dict(dimension=dimension, scale_to_base=scale)
            for u, (dimension, scale) in sorted(UNITS.items())
        },
        special_units=dict(
            deg="angle in degrees", rad="angle in radians", dB="referenced amplitude logarithm"
        ),
        unit_policy="Case-sensitive; registry is conversion support, not permission "
        "for every operation. "
        "Consult each quantity/output rule. Real single-waveform operations also allow "
        "identical nonempty unknown units. AC phase supports deg/rad; dB is never "
        "implicitly converted to/from linear values.",
        entries=entries,
    )
    return dict(catalog, catalog_revision=digest(catalog))


def call_catalog(name, args):
    try:
        schema = next(t["inputSchema"] for t in CATALOG_TOOLS if t["name"] == name)
        validate(args, schema)
    except (CircuitSpecError, StopIteration) as exc:
        raise ResultError(str(exc)) from exc
    catalog = build_catalog()
    revision = catalog["catalog_revision"]
    if name == "get_measurement_capability":
        if args.get("catalog_revision", revision) != revision:
            raise ResultError("measurement catalog revision changed; repeat capability discovery")
        entries = [e for e in catalog["entries"] if e["capability_id"] == args["capability_id"]]
        if not entries:
            raise ResultError("unknown measurement capability id")
        return dict(
            ok=True,
            catalog_revision=revision,
            capability=entries[0],
            unit_registry=catalog["unit_registry"],
            special_units=catalog["special_units"],
            unit_policy=catalog["unit_policy"],
        )
    filters = {k: args[k] for k in ("kind", "operation", "tool_name") if k in args}
    query = args.get("query", "").casefold()
    entries = [
        e
        for e in catalog["entries"]
        if all(e[k] == v for k, v in filters.items())
        and query in " ".join(e[k] for k in ("capability_id", "tool_name", "definition")).casefold()
    ]
    start, limit = args.get("offset", 0), args.get("limit", 20)
    keys = (
        "capability_id",
        "kind",
        "operation",
        "tool_name",
        "definition",
        "analysis",
        "output_unit",
    )
    return dict(
        ok=True,
        schema=CATALOG_SCHEMA,
        catalog_revision=revision,
        semantics_version=SEMANTICS_VERSION,
        scope=catalog["scope"],
        total=len(entries),
        items=[{k: e[k] for k in keys} for e in entries[start : start + limit]],
        next_offset=start + limit if start + limit < len(entries) else None,
    )
