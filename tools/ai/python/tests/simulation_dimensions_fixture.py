"""Explicit numeric fixtures for generic sweeps/corners; not product recipes."""

from simulation_recipe_fixture import recipe


def dimension_recipe(model=None, cell="matrix"):
    data = recipe(cell=cell)
    data["variables"] = dict(RVAL="2K", CVAL="1n", OFFSET="0")
    for test in data["tests"]:
        test["variables"] = {}
    data["corners"] = [
        dict(
            name=n,
            temperature_c=t,
            variables=dict(OFFSET=o),
            models=[dict(path=str(model), section=n)] if model else [],
        )
        for n, t, o in [("tt", 27, 0), ("ff", -40, 1), ("ss", 125, -1)]
    ]
    data["sweeps"] = [
        dict(variable="RVAL", values=[2000, 4000]),
        dict(variable="CVAL", values=[1e-9, 2e-9]),
    ]
    return data
