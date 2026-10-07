"""aivw command dispatch, environment detachment and error mapping."""

from __future__ import annotations

import json
import os
import sys
from sicoentry import compiled_module
from . import COMMAND_NAME
from .errors import AivwError
from .toolchain import detach_mps_environment
from .cli_parser import build_parser as build_parser
from .cli_inspection import runtime_info, info, profiles, pilot, doctor
from .cli_recipe import recipes, recipe, run_recipe
from .cli_structure import check_connectivity
from .cli_manifest import verify_run
from .cli_text_probe import probe_text_view
from .cli_ldo import qualify_ldo, qualify_ldo_live, plan_ldo_exploration
from .cli_gates import run, qualify
from .cli_qualification import qualify_provider, qualify_revision
from .cli_external import qualify_external_provider


def main(argv: list[str] | None = None) -> int:
    if compiled_module(__file__):
        from .agent.runtime_info import validate_production_runtime
        try:
            validate_production_runtime()
        except RuntimeError as exc:
            print(f"{COMMAND_NAME}: {exc}", file=sys.stderr)
            return 2
    detach_mps_environment(os.environ)
    args = build_parser().parse_args(argv)
    try:
        if args.command == "info":
            return info(args)
        if args.command == "profiles":
            return profiles(args)
        if args.command == "recipes":
            return recipes(args)
        if args.command == "recipe":
            return recipe(args)
        if args.command == "run-recipe":
            return run_recipe(args)
        if args.command == "check-connectivity":
            return check_connectivity(args)
        if args.command == "verify-run":
            return verify_run(args)
        if args.command == "probe-text-view":
            return probe_text_view(args)
        if args.command == "pilot":
            return pilot(args)
        if args.command == "doctor":
            return doctor(args)
        if args.command == "runtime-info":
            return runtime_info(args)
        if args.command == "qualify":
            return qualify(args)
        if args.command == "qualify-ldo":
            return qualify_ldo(args)
        if args.command == "qualify-ldo-live":
            return qualify_ldo_live(args)
        if args.command == "plan-ldo-exploration":
            return plan_ldo_exploration(args)
        if args.command == "qualify-provider":
            return qualify_provider(args)
        if args.command == "qualify-revision":
            return qualify_revision(args)
        if args.command == "qualify-external-provider":
            return qualify_external_provider(args)
        if args.command == "run":
            return run(args)
        raise AssertionError(args.command)
    except KeyboardInterrupt:
        return 130
    except (AivwError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"{COMMAND_NAME}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
