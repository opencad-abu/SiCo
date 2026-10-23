"""Plan generic Maestro recipes and dispatch fixed lifecycle operations."""

import hashlib
from pathlib import Path

from .circuit_create import DIGEST, REF, REQUEST, skill_literal
from .circuit_schema import tool
from .circuit_spec_schema import CircuitSpecError, canonical, digest, unique, validate
from .skill_result import call_skill
from .simulation_dimensions import dimension_plan, native_dimensions
from .simulation_schema import OPTIONS, RECIPE, VIEW_DEFAULTS, native_options, option_problems

SIMULATION_TOOLS = [
    tool(
        "preview_simulation_recipe",
        "Validate Spectre multi-test/corner/sweep recipe locally. No OA writes or expression eval. "
        "Returns recipe_digest, expected points, unverified dependencies. Variables, temperature, "
        "analyses and outputs are required; empty test.models inherit ADE session model files "
        "(init_pdk/libInit), listed models are explicit content identities. Expression outputs need "
        "plot=true for RDB evaluation; save alone is insufficient. Corners define shared model "
        "lists/temperature with empty test.models; sweeps give SI values for declared globals without "
        "local or corner override. Max 64 sweep/256 test-corner points. Saved schematic or "
        "retained config_ref matching Config lists; no Monte Carlo.",
        {"recipe": RECIPE},
        ("recipe",),
    ),
    tool(
        "create_simulation_setup",
        "Create a NEW Maestro from an exact previewed recipe and confirmed "
        "task_ref. Check source schematics/model files, configure and verify each test, save, "
        "reopen and read back. Retain foreground UI. No simulation; never overwrite. "
        "Identical request_id returns retained status, including partial failures.",
        {"recipe": RECIPE, "recipe_digest": DIGEST, "task_ref": REF, "request_id": REQUEST},
        ("recipe", "recipe_digest", "task_ref", "request_id"),
        True,
    ),
    tool(
        "inspect_simulation_setup",
        "Verify retained setup, corner/sweep settings, sources and explicitly listed "
        "model files without saving user edits. Returns setup summary and result context_ref.",
        {"setup_ref": REF},
        ("setup_ref",),
    ),
    tool(
        "run_simulation_setup",
        "Run this unchanged retained recipe with existing site job policy. "
        "ADE evaluates output expressions. Asynchronous exact history; same request_id never "
        "launches again. Status/results distinguish completion from specification qualification.",
        {"setup_ref": REF, "request_id": REQUEST},
        ("setup_ref", "request_id"),
        True,
    ),
    tool(
        "get_simulation_recipe_status",
        "Read this exact retained history progress and test states; "
        "no inferred pass from launch. Does not close the foreground window.",
        {"run_ref": REF},
        ("run_ref",),
    ),
    tool(
        "stop_simulation_recipe",
        "Request cancellation of the exact retained history, preserve UI; "
        "poll status afterwards. A stop request does not prove simulator process termination.",
        {"run_ref": REF},
        ("run_ref",),
        True,
    ),
    tool(
        "release_simulation_setup",
        "Detach an idle unchanged foreground setup while keeping UI open, "
        "or close only the owned unmodified background session. Failed/unknown state retained.",
        {"setup_ref": REF},
        ("setup_ref",),
        True,
    ),
]
SIMULATION_NAMES = frozenset(t["name"] for t in SIMULATION_TOOLS)


def with_view_defaults(recipe):
    """One authoritative fill: each test without view lists uses the project list."""
    if not isinstance(recipe, dict) or not isinstance(recipe.get("tests"), list):
        return recipe
    return dict(recipe, tests=[
        {**VIEW_DEFAULTS, **test} if isinstance(test, dict) else test
        for test in recipe["tests"]
    ])


def preview_recipe(recipe):
    recipe = with_view_defaults(recipe)
    validate(recipe, RECIPE)
    if len(canonical(recipe).encode()) > 48000:
        raise CircuitSpecError("simulation recipe exceeds 48 KiB")
    unique(recipe["tests"], "name", "tests")
    for test in recipe["tests"]:
        if (
            test["design"]["view"] == recipe["target"]["view"]
            and test["design"] == recipe["target"]
        ):
            raise CircuitSpecError("setup cannot replace its source design")
        unique(test["analyses"], "name", "analyses")
        unique(test["outputs"], "name", "outputs")
        problems = option_problems(test.get("simulator_options", {}))
        if problems:
            raise CircuitSpecError("; ".join(problems[:3]))
        if len({(m["path"], m.get("section")) for m in test["models"]}) != len(test["models"]):
            raise CircuitSpecError("duplicate model inclusion")
        for analysis in test["analyses"]:
            name, opts = analysis["name"], analysis["options"]
            if set(opts) - OPTIONS[name]:
                raise CircuitSpecError("unsupported " + name + " analysis option")
            if name == "tran" and "stop" not in opts:
                raise CircuitSpecError("tran requires explicit stop")
            if name == "ac" and not ({"start", "stop"} <= set(opts)):
                raise CircuitSpecError("ac requires explicit start/stop")
            if name == "ac" and len(set(opts) & {"dec", "lin", "log", "values"}) > 1:
                raise CircuitSpecError("ac requires a single frequency step specification")
            for key, value in opts.items():
                if key in {"saveOppoint", "skipdc"}:
                    if type(value) is not bool:
                        raise CircuitSpecError(key + " requires boolean")
                elif not isinstance(value, str):
                    raise CircuitSpecError(key + " requires an opaque ADE string")
        for output in test["outputs"]:
            if output["kind"] == "signal":
                parts = output["value"].split("/")
                terminal = output.get("signal_type", "net") != "net"
                if (
                    parts[0] or not all(parts[1:]) or len(parts) < 2
                    or len(parts) > (11 if terminal else 10)
                ):
                    raise CircuitSpecError(
                        "signal output requires an absolute path with nonempty components "
                        "and at most 8 hierarchy levels: " + output["name"])
                if terminal and len(parts) < 3:
                    raise CircuitSpecError(
                        "terminal output requires /instance/terminal: " + output["name"])
            if output["kind"] != "signal" and "signal_type" in output:
                raise CircuitSpecError(
                    "signal_type is only supported for kind=signal: " + output["name"])
        for values in (recipe["variables"], test["variables"]):
            if "temperature" in values:
                raise CircuitSpecError("use temperature_c instead of a temperature variable")
            if any(any(c.isspace() or c in ":,[];" for c in v) for v in values.values()):
                raise CircuitSpecError(
                    "v1 variables must be scalar expressions without whitespace or sweep delimiters"
                )
        # Config-owned tests inherit their hierarchy lists from the Config;
        # direct-schematic tests must be able to descend into their own design.
        if "config_ref" not in test and test["design"]["view"] not in test["switch_views"]:
            raise CircuitSpecError(
                "switch_views must include the test design view: " + test["design"]["view"])
    return {
        "ok": True,
        "recipe_digest": digest(recipe),
        "recipe": recipe,
        "warnings": [
            {
                "code": "expression_not_selected",
                "test": test["name"],
                "output": output["name"],
                "message": "plot=false leaves this expression unselected; save=true alone "
                "does not produce an evaluated RDB result. "
                "Set plot=true if this result is required.",
            }
            for test in recipe["tests"]
            for output in test["outputs"]
            if output["kind"] == "expression" and not output["plot"]
        ],
        **dimension_plan(recipe),
        "live_verified": False,
        "models_verified": False,
        "simulation_qualified": False,
    }


def model_identities(recipe):
    rows = []
    for path in sorted(
        {m["path"] for t in [*recipe["tests"], *recipe.get("corners", [])] for m in t["models"]}
    ):
        p = Path(path)
        if not p.is_file():
            raise CircuitSpecError("model file missing: " + path)
        h = hashlib.sha256()
        with p.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        rows.append([path, h.hexdigest()])
    return rows


def analysis_options(analysis):
    options = dict(analysis["options"])
    # maeGetAnalysis documents the ADE selector fields. Setting dec alone leaves
    # Automatic selected and Spectre silently uses its default ten points/decade.
    if analysis["name"] == "ac" and "dec" in options:
        options.update(incrType="Logarithmic", stepTypeLog="Points Per Decade")
    return options


def native_recipe(recipe):
    def pairs(mapping):
        return [[k, v] for k, v in sorted(mapping.items())]

    payload = [
        [recipe["target"][k] for k in ("library", "cell", "view")],
        pairs(recipe["variables"]),
        str(recipe["temperature_c"]),
        [
            [
                t["name"],
                [t["design"][k] for k in ("library", "cell", "view")],
                t["simulator"],
                pairs(t["variables"]),
                [[m["path"]] + ([m["section"]] if "section" in m else []) for m in t["models"]],
                [[a["name"], pairs(analysis_options(a))] for a in t["analyses"]],
                [[o[k] for k in ("name", "kind", "value", "plot", "save")]
                 + ([o["signal_type"]] if "signal_type" in o else []) for o in t["outputs"]],
                t["switch_views"],
                t["stop_views"],
            ]
            + ([t["config_ref"]] if "config_ref" in t else [])
            + ([native_options(t["simulator_options"])] if t.get("simulator_options") else [])
            for t in recipe["tests"]
        ],
    ]
    if recipe.get("corners") or recipe.get("sweeps"):
        payload.extend(native_dimensions(recipe))
    return payload


def call_simulation(name, args, client):
    schema = next(t["inputSchema"] for t in SIMULATION_TOOLS if t["name"] == name)
    validate(args, schema)
    if name == "preview_simulation_recipe":
        return True, preview_recipe(args["recipe"])
    if name == "create_simulation_setup":
        recipe = with_view_defaults(args["recipe"])
        preview = preview_recipe(recipe)
        if preview["recipe_digest"] != args["recipe_digest"]:
            raise CircuitSpecError("recipe_digest differs; preview exact recipe")
        fn = "aiSimCreate"
        vals = [
            args["task_ref"],
            args["request_id"],
            args["recipe_digest"],
            native_recipe(recipe),
            model_identities(recipe),
        ]
    else:
        fn = {
            "inspect_simulation_setup": "aiSimInspect",
            "run_simulation_setup": "aiSimRun",
            "get_simulation_recipe_status": "aiSimStatus",
            "stop_simulation_recipe": "aiSimStop",
            "release_simulation_setup": "aiSimRelease",
        }[name]
        vals = [args.get("setup_ref", args.get("run_ref"))]
        if name in {"inspect_simulation_setup", "run_simulation_setup"}:
            # Obtain the retained explicit file list; run requests cannot supply file paths.
            ok, reply = call_simulation_native(client, "aiSimModels", vals)
            if not ok:
                return ok, reply
            actual = model_identities(
                {"tests": [{"models": [{"path": p[0]} for p in reply["models"] or []]}]}
            )
            vals.append(actual)
        if "request_id" in args:
            vals.append(args["request_id"])
    return call_simulation_native(client, fn, vals)


def call_simulation_native(client, fn, values):
    code = fn + "(" + " ".join(skill_literal(v) for v in values) + ")"
    if len(code.encode()) > 65536:
        raise CircuitSpecError("native recipe request exceeds 64 KiB")
    return call_skill(client, code, native=True)
