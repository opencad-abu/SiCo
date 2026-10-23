"""Explicit, bounded Spectre setup recipe; no project/PDK defaults."""

import math

from .circuit_schema import string_schema
from .circuit_spec_schema import ID, TARGET, array, enum, mapping, obj

TEXT = string_schema(1024)
PATH = string_schema(2048, pattern=r"^/[^\x00-\x1f]+$")
VALUE = {"type": ["string", "number", "boolean"], "maxLength": 1024}
VARIABLES = mapping(TEXT, maximum=64)
ANALYSIS = obj({"name": enum("dc", "ac", "tran"), "options": mapping(VALUE, maximum=24)})
# Simulator options the recipe consumer can apply to a test; PDK setup.options
# entries outside this registry stay unsupported instead of being dropped.
SIMULATOR_OPTIONS = {"scale": {"type": "number", "exclusiveMinimum": 0, "maximum": 1000,
                                  "native": "string"}}


def native_options(options):
    """The option pairs as the ADE SKILL surface expects them.

    ADE simulator options are string-valued (spectre's scale rejects a number
    with ADE-5007), so registry entries declaring native=="string" are
    formatted here once instead of at every call site.
    """
    rows = []
    for name, value in sorted(options.items()):
        spec = SIMULATOR_OPTIONS.get(name) or {}
        rows.append([name, ("%g" % value) if spec.get("native") == "string" else value])
    return rows


def option_problems(options):
    """Unsupported or invalid entries of one explicit simulator-option mapping."""
    if not isinstance(options, dict):
        return ["simulator options must be a mapping"]
    problems = []
    for name, value in sorted(options.items(), key=lambda row: str(row[0])):
        spec = SIMULATOR_OPTIONS.get(name)
        if not spec:
            problems.append("unsupported simulator option: " + str(name))
            continue
        if spec["type"] == "number" and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not spec["exclusiveMinimum"] < value <= spec["maximum"]):
            problems.append("invalid value for simulator option: " + str(name))
    return problems
OUTPUT = obj(
    {
        "name": ID,
        "kind": enum("signal", "expression"),
        "value": TEXT,
        "signal_type": {
            **enum("net", "terminal", "terminalV"),
            "description": "For kind=signal: net voltage (default), terminal current, "
            "or terminalV voltage. Instance pin paths such as /V0/PLUS require "
            "terminal or terminalV. Scalar paths are resolved read-only before setup creation "
            "through saved schematics or the retained Config bindings (maximum depth 8).",
        },
        "plot": {
            "type": "boolean",
            "description": "ADE output selection. For expression outputs, true is required "
            "to produce point-expression RDB results in the supported Assembler workflow. "
            "false retains the definition but save=true alone does not evaluate it.",
        },
        "save": {"type": "boolean"},
    },
    ("name", "kind", "value", "plot", "save"),
)
MODEL = obj(
    {"path": PATH, "section": string_schema(96, pattern=r"^[A-Za-z_][A-Za-z0-9_.-]*$")}, ("path",)
)
TEMPERATURE = {"type": "number", "minimum": -273.14, "maximum": 1000}
NUMERIC = {"type": "number", "minimum": -1e100, "maximum": 1e100}
SWEEP = obj({"variable": ID, "values": array(NUMERIC, 16, 2)})
CORNER = obj(
    {
        "name": ID,
        "temperature_c": TEMPERATURE,
        "variables": mapping(NUMERIC, maximum=64),
        "models": array(MODEL, 32),
    }
)
TEST = obj(
    {
        "name": ID,
        "design": TARGET,
        "config_ref": string_schema(128, pattern=r"^config:[A-Za-z0-9_-]+$"),
        "simulator": enum("spectre"),
        "variables": VARIABLES,
        "models": array(MODEL, 32),
        "analyses": array(ANALYSIS, 3, 1),
        "outputs": array(OUTPUT, 64, 1),
        "simulator_options": mapping(VALUE, maximum=16),
        "switch_views": array(ID, 16, 1),
        "stop_views": array(ID, 16, 1),
    },
    (
        "name",
        "design",
        "simulator",
        "variables",
        "models",
        "analyses",
        "outputs",
    ),
)
RECIPE = obj(
    {
        "schema": enum("cad.simulation.recipe.v1"),
        "target": TARGET,
        "variables": VARIABLES,
        "temperature_c": TEMPERATURE,
        "tests": array(TEST, 8, 1),
        "corners": array(CORNER, 8, 1),
        "sweeps": array(SWEEP, 4, 1),
    },
    ("schema", "target", "variables", "temperature_c", "tests"),
)
# Deliberately small first set. Runtime getters must also verify accepted values.
OPTIONS = {
    "dc": {"saveOppoint"},
    "ac": {"start", "stop", "dec", "lin", "log", "values"},
    "tran": {"stop", "start", "step", "maxstep", "errpreset", "skipdc"},
}

# Project view-list defaults, kept identical to create_circuit_config's
# switch_views/stop_views.  ADE searches the list in order: builtin
# analogLib/basic primitives (res, vdc, vpulse, gnd) have no schematic view,
# so a list without `spectre` cannot netlist them.
VIEW_DEFAULTS = {"switch_views": ["spectre", "schematic"], "stop_views": ["spectre"]}
