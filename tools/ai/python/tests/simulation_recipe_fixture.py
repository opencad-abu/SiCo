"""Explicit acceptance recipe; these values are never production defaults."""

from copy import deepcopy


def recipe(library="genericWork", cell="nominal_setup", model=None):
    tests = []
    for name in ("settling", "bias"):
        tests.append(
            {
                "name": name,
                "design": {"library": library, "cell": "network", "view": "schematic"},
                "simulator": "spectre",
                "variables": {"RVAL": "4K"} if name == "settling" else {},
                "models": [{"path": str(model), "section": "tt"}] if model else [],
                "analyses": (
                    [{"name": "tran", "options": {"stop": "32u", "errpreset": "conservative"}}]
                    if name == "settling"
                    else [{"name": "dc", "options": {"saveOppoint": True}}]
                ),
                "outputs": [
                    {
                        "name": "voltage",
                        "kind": "expression",
                        "value": 'value(VT("/OUT") 12e-6)' if name == "settling" else 'VDC("/OUT")',
                        "plot": True,
                        "save": True,
                    },
                    {"name": "OUT", "kind": "signal", "value": "/OUT", "plot": True, "save": True},
                ],
                "switch_views": ["spectre", "schematic"],
                "stop_views": ["spectre"],
            }
        )
    return deepcopy(
        {
            "schema": "cad.simulation.recipe.v1",
            "target": {"library": library, "cell": cell, "view": "maestro"},
            "variables": {"RVAL": "2K"},
            "temperature_c": 31,
            "tests": tests,
        }
    )
