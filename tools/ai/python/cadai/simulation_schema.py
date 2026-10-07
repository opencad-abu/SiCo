"""Explicit, bounded Spectre setup recipe; no project/PDK defaults."""

from .circuit_schema import string_schema
from .circuit_spec_schema import ID, TARGET, array, enum, mapping, obj

TEXT = string_schema(1024)
PATH = string_schema(2048, pattern=r"^/[^\x00-\x1f]+$")
VALUE = {"type": ["string", "number", "boolean"], "maxLength": 1024}
VARIABLES = mapping(TEXT, maximum=64)
ANALYSIS = obj({"name": enum("dc", "ac", "tran"), "options": mapping(VALUE, maximum=24)})
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
        "switch_views",
        "stop_views",
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
