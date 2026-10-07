"""Generic recipe description and bounded execution commands."""

from __future__ import annotations

import argparse
from .errors import AivwError
from .profiles import load_profile
from .workspace import resolve_launch_paths
from .cli_output import print_json


def recipes(args: argparse.Namespace) -> int:
    from .recipe import available_recipes

    values = available_recipes()
    if args.json:
        print_json({"recipes": list(values)})
    else:
        print("\n".join(values))
    return 0


def recipe(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .recipe import load_builtin_recipe, load_recipe
    from .registry import builtin_registry

    candidate = Path(args.reference).expanduser()
    registry = builtin_registry()
    recipe = (
        load_recipe(candidate, registry=registry)
        if candidate.is_file()
        else load_builtin_recipe(args.reference, registry=registry)
    )
    payload = recipe.summary()
    payload["registry"] = registry.describe()
    if args.json:
        print_json(payload)
    else:
        print(f"status: {payload['status']}")
        print(f"recipe: {payload['recipe_id']} revision={payload['revision']}")
        print(f"target: {payload['target']}")
    return 0


def run_recipe(args: argparse.Namespace) -> int:
    from pathlib import Path

    from .recipe import load_builtin_recipe, load_recipe
    from .recipe_dag import workflow_dependency_order
    from .recipe_runner import run_recipe_dry_run, run_recipe_through
    from .registry import builtin_registry

    if args.dry_run and args.through:
        raise AivwError("--dry-run and --through are mutually exclusive")
    candidate = Path(args.reference).expanduser()
    registry = builtin_registry()
    recipe = (
        load_recipe(candidate, registry=registry)
        if candidate.is_file()
        else load_builtin_recipe(args.reference, registry=registry)
    )
    if not args.dry_run and not args.through:
        raise AivwError(
            "select --dry-run or a bounded execution target with --through GATE"
        )
    profile = load_profile(args.profile)
    launch = resolve_launch_paths()
    if args.dry_run:
        manifest = run_recipe_dry_run(profile, launch, recipe, registry=registry)
    else:
        selected = workflow_dependency_order(
            recipe.payload["workflow"], (args.through,)
        )
        gates = {item["id"]: item for item in recipe.payload["workflow"]["gates"]}
        if any(
            gates[gate_id]["executor"] == "virtuoso.publish_text_view"
            for gate_id in selected
        ):
            raise AivwError(
                "publication is not available through run-recipe; it requires a separate "
                "human-approved promotion command"
            )
        from .toolchain import capture_module_environment, probe_tools

        environment = capture_module_environment(profile.modules)
        qualified = probe_tools(profile.tools, environment)
        tools = {item.name: item.path for item in qualified if item.status == "PASS"}
        manifest = run_recipe_through(
            profile,
            launch,
            recipe,
            through=args.through,
            registry=registry,
            environment=environment,
            tools=tools,
            tool_evidence=[item.to_dict() for item in qualified],
            timeout=args.timeout,
        )
    if args.json:
        print_json(manifest)
    else:
        print(f"status: {manifest['status']}")
        print(f"run_dir: {manifest['run_dir']}")
        print(f"payload_dir: {manifest['details']['payload_root']}")
        print("design_verdict: NOT_RUN")
    return 0 if manifest["status"] in {"PASS", "DRY_RUN_PASS"} else 1
