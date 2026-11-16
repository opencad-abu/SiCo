"""Draft compiler for the package-level layout process view.

The process view is a read-only, content-addressed projection used by layout
engines. Engine-specific views remain opaque under ``engine_views`` until the
corresponding backend adapter is implemented.
"""

import math
import re

from .jsonio import fail, fingerprint
from .layout import validate_process
from .tech_units import validate_tech_units
from .validate import fields

SCHEMA = "sico.process_view.v1"
COMPILER_VERSION = "sico-layout-process/0.1"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _positive(value, label):
    if (type(value) not in (int, float) or isinstance(value, bool)
            or not math.isfinite(value) or value <= 0):
        fail(label + " must be positive")
    return value


def _string(value, label):
    if not isinstance(value, str) or not value:
        fail(label + " must be a non-empty string")
    return value


def _square_grid_um(process):
    step = process["grid"]["step"]
    if step[0] != step[1]:
        fail("process view v1 requires a square layout grid")
    return step[0] * 1e6


def compile_process_view(
    process,
    *,
    view_id,
    version,
    pdk,
    tech_lib,
    stream_layer_map=None,
    engine_views=None,
    artifacts=None,
    tech_units=None,
    compiler_version=COMPILER_VERSION,
):
    """Compile one validated layout.json into sico.process_view.v1."""
    validate_process(process)
    _string(view_id, "process view id")
    _string(version, "process view version")
    _string(pdk, "process view pdk")
    _string(tech_lib, "process view tech_lib")
    stream_layer_map = (process["stream"]["layer_map_ref"]
                        if stream_layer_map is None else stream_layer_map)
    _string(stream_layer_map, "stream_layer_map")
    _string(compiler_version, "compiler_version")
    engine_views = {} if engine_views is None else engine_views
    artifacts = [] if artifacts is None else artifacts
    if not isinstance(engine_views, dict) or any(
            not isinstance(key, str) or not isinstance(value, dict)
            for key, value in engine_views.items()):
        fail("engine_views must map names to objects")
    if not isinstance(artifacts, list) or any(not isinstance(item, dict) for item in artifacts):
        fail("artifacts must be an array of objects")
    dbu_per_um = round(1e-6 / process["database_unit"])
    if abs(dbu_per_um * process["database_unit"] - 1e-6) > 1e-18:
        fail("database_unit is incompatible with an integer dbu_per_um")
    grid_um = _square_grid_um(process)
    if tech_units is None:
        stream_unit_m = process["database_unit"] * dbu_per_um
        stream_precision_m = process["database_unit"]
        units_source = {"source": "layout_process_data"}
    else:
        tech_units = validate_tech_units(tech_units)
        if abs(float(tech_units["database_unit"]) - process["database_unit"]) > 1e-18:
            fail("tech.db database_unit disagrees with layout process data")
        if int(round(float(tech_units["dbu_per_um"]))) != dbu_per_um:
            fail("tech.db dbu_per_um disagrees with layout process data")
        if abs(float(tech_units["grid_um"]) - grid_um) > 1e-12:
            fail("tech.db grid_um disagrees with layout process data")
        stream_unit_m = tech_units["stream_unit_m"]
        stream_precision_m = tech_units["stream_precision_m"]
        units_source = tech_units
    view = {
        "schema": SCHEMA,
        "id": view_id,
        "version": version,
        "pdk": pdk,
        "tech_lib": tech_lib,
        "database_unit": process["database_unit"],
        "dbu_per_um": dbu_per_um,
        "stream_unit_m": stream_unit_m,
        "stream_precision_m": stream_precision_m,
        "units_source": units_source,
        "grid_um": grid_um,
        "stream_layer_map": stream_layer_map,
        "source_digest": fingerprint(process),
        "compiler_version": compiler_version,
        "artifacts": artifacts,
        "engine_views": engine_views,
    }
    validate_process_view(view)
    return view


def validate_process_view(value):
    fields(value, ("schema", "id", "version", "pdk", "tech_lib", "database_unit",
                   "dbu_per_um", "grid_um", "stream_layer_map", "source_digest",
                   "compiler_version", "artifacts", "engine_views"),
           ("x_", "stream_unit_m", "stream_precision_m", "units_source"))
    if value["schema"] != SCHEMA:
        fail("Unsupported process view schema")
    for key in ("id", "version", "pdk", "tech_lib", "stream_layer_map", "compiler_version"):
        _string(value[key], key)
    _positive(value["database_unit"], "database_unit")
    _positive(value["dbu_per_um"], "dbu_per_um")
    _positive(value["grid_um"], "grid_um")
    for key in ("stream_unit_m", "stream_precision_m"):
        if key in value:
            _positive(value[key], key)
    if "units_source" in value and not isinstance(value["units_source"], dict):
        fail("units_source must be an object")
    if (not isinstance(value["source_digest"], str)
            or not DIGEST.fullmatch(value["source_digest"])):
        fail("source_digest must be sha256:<64 lowercase hex>")
    if (not isinstance(value["artifacts"], list)
            or any(not isinstance(item, dict) for item in value["artifacts"])):
        fail("artifacts must be an array of objects")
    if (not isinstance(value["engine_views"], dict)
            or any(not isinstance(key, str) or not isinstance(item, dict)
                   for key, item in value["engine_views"].items())):
        fail("engine_views must map names to objects")
    return value
