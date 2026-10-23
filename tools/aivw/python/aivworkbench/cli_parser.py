"""Argument declarations for the aivw command."""

from __future__ import annotations

import argparse
from . import COMMAND_NAME, PRODUCT_NAME, __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=COMMAND_NAME, description=PRODUCT_NAME)
    parser.add_argument(
        "--version", action="version", version=f"{COMMAND_NAME} {__version__}"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    info = sub.add_parser("info", help="show the current $CWD/.aivw launch context")
    info.add_argument("--profile", default="amsverify")
    info.add_argument("--json", action="store_true")

    profiles = sub.add_parser("profiles", help="list checked-in project profiles")
    profiles.add_argument("--json", action="store_true")

    recipes = sub.add_parser("recipes", help="list checked-in generic workflow recipes")
    recipes.add_argument("--json", action="store_true")

    recipe = sub.add_parser("recipe", help="validate and describe one workflow recipe")
    recipe.add_argument("reference", help="checked-in recipe ID or a recipe JSON path")
    recipe.add_argument("--json", action="store_true")

    run_recipe = sub.add_parser(
        "run-recipe",
        help="run a target-independent recipe through the generic workflow kernel",
    )
    run_recipe.add_argument(
        "reference", help="checked-in recipe ID or a recipe JSON path"
    )
    run_recipe.add_argument("--profile", default="amsverify")
    run_recipe.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and allocate split storage without executing any EDA or AI gate",
    )
    run_recipe.add_argument(
        "--through",
        metavar="GATE",
        help="execute exactly this gate and its dependency closure; publication is excluded",
    )
    run_recipe.add_argument("--timeout", type=float, default=900.0)
    run_recipe.add_argument("--json", action="store_true")

    connectivity = sub.add_parser(
        "check-connectivity",
        help="compare an official SI inventory with a structural SV candidate",
    )
    connectivity.add_argument("--netlist", action="append", required=True)
    connectivity.add_argument("--map", dest="map_path", required=True)
    connectivity.add_argument("--globalmap", required=True)
    connectivity.add_argument("--candidate", required=True)
    connectivity.add_argument("--module", required=True)
    connectivity.add_argument("--port", action="append", default=[])
    connectivity.add_argument("--json", action="store_true")

    verify_run = sub.add_parser(
        "verify-run",
        help="verify a split control/payload manifest pair and indexed artifacts",
    )
    verify_run.add_argument("--control-manifest", required=True)
    verify_run.add_argument("--payload-manifest", required=True)
    verify_run.add_argument("--no-artifacts", action="store_true")
    verify_run.add_argument("--json", action="store_true")

    text_probe = sub.add_parser(
        "probe-text-view",
        help="qualify create/update/readback/restore in a disposable SystemVerilog library",
    )
    text_probe.add_argument("--profile", default="amsverify")
    text_probe.add_argument("--timeout", type=float, default=300.0)
    text_probe.add_argument("--json", action="store_true")

    pilot = sub.add_parser("pilot", help="describe an approved qualification pilot")
    pilot.add_argument("name", choices=("m0-e", "m0-s", "m1-ai"))
    pilot.add_argument("--profile", default="amsverify")
    pilot.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor", help="qualify modules, tools, and pilot inputs")
    doctor.add_argument("--profile", default="amsverify")
    doctor.add_argument("--json", action="store_true")

    runtime_info = sub.add_parser(
        "runtime-info",
        help="report the configured production Python and bundle capabilities",
    )
    runtime_info.add_argument("--json", action="store_true")

    qualify = sub.add_parser(
        "qualify", help="qualify inputs for a pilot without running it"
    )
    qualify.add_argument("name", choices=("m1-ai",))
    qualify.add_argument("--profile", default="amsverify")
    qualify.add_argument("--timeout", type=float, default=120.0)
    qualify.add_argument("--json", action="store_true")

    qualify_ldo = sub.add_parser(
        "qualify-ldo",
        help="run the read-only LDO source/PDK preflight without starting EDA tools",
    )
    qualify_ldo.add_argument("--profile", default="amsverify")
    qualify_ldo.add_argument("--json", action="store_true")

    ldo_live = sub.add_parser(
        "qualify-ldo-live",
        help="collect read-only LDO dbAccess/HED evidence in split storage",
    )
    ldo_live.add_argument("--profile", default="amsverify")
    ldo_live.add_argument(
        "--no-runams",
        action="store_true",
        help="stop after dbAccess and HED evidence; do not invoke runams",
    )
    ldo_live.add_argument("--timeout", type=float, default=300.0)
    ldo_live.add_argument("--json", action="store_true")

    ldo_explore = sub.add_parser(
        "plan-ldo-exploration",
        help="build a source-derived exploratory LDO characterization plan without running EDA",
    )
    ldo_explore.add_argument("policy", help="exploratory policy JSON path")
    ldo_explore.add_argument("--output", help="optional new JSON output path")
    ldo_explore.add_argument("--json", action="store_true")

    run = sub.add_parser("run", help="run an approved qualification pilot or gate")
    run.add_argument("name", choices=("m0-e", "m0-s", "m1-ai"))
    run.add_argument(
        "--gate",
        choices=(
            "golden-nominal",
            "golden-pvt",
            "rnm",
            "rnm-evidence",
            "spectre-evidence",
            "correlation",
        ),
        help="required for m1-ai; runs only the named qualification gate",
    )
    run.add_argument("--spectre-evidence", help="paired correlation evidence JSON")
    run.add_argument("--rnm-evidence", help="paired correlation evidence JSON")
    run.add_argument(
        "--rnm-source-manifest", help="PASS RNM manifest used for evidence export"
    )
    run.add_argument(
        "--spectre-source-manifest",
        help="PASS Spectre golden manifest used for evidence export",
    )
    run.add_argument("--policy", help="override the correlation policy JSON")
    run.add_argument("--profile", default="amsverify")
    run.add_argument("--timeout", type=float, default=900.0)
    run.add_argument("--json", action="store_true")
    return parser
