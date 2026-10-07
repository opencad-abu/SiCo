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

    provider_qualify = sub.add_parser(
        "qualify-provider",
        help="qualify an offline Replay or Bundle provider against strict request fixtures",
    )
    provider_qualify.add_argument(
        "--provider", choices=("replay", "bundle"), required=True
    )
    provider_qualify.add_argument(
        "--requests", required=True, help="strict JSON/JSONL ProviderRequest fixture"
    )
    provider_qualify.add_argument(
        "--records", help="strict JSON/JSONL ReplayRecord fixture"
    )
    provider_qualify.add_argument(
        "--bundle", help="relocatable response bundle directory"
    )
    provider_qualify.add_argument("--source-generation")
    provider_qualify.add_argument("--template-lock")
    provider_qualify.add_argument(
        "--expected-action-kind",
        action="append",
        dest="expected_action_kinds",
        default=[],
        help="restrict accepted provider actions; may be repeated",
    )
    provider_qualify.add_argument(
        "--report", required=True, help="absolute qualification report path"
    )
    provider_qualify.add_argument("--overwrite", action="store_true")
    provider_qualify.add_argument("--timeout", type=float, default=20.0)
    provider_qualify.add_argument("--json", action="store_true")

    revision_qualify = sub.add_parser(
        "qualify-revision",
        help="run a bounded candidate/revision qualification with Rule or Replay provider",
    )
    revision_qualify.add_argument(
        "--provider", choices=("rule", "replay"), required=True
    )
    revision_qualify.add_argument(
        "--context", required=True, help="strict JSON candidate context object"
    )
    revision_qualify.add_argument(
        "--records", help="strict JSON/JSONL ReplayRecord fixture"
    )
    revision_qualify.add_argument("--source-generation", required=True)
    revision_qualify.add_argument("--template-lock")
    revision_qualify.add_argument(
        "--report", required=True, help="absolute qualification report path"
    )
    revision_qualify.add_argument("--overwrite", action="store_true")
    revision_qualify.add_argument("--timeout", type=float, default=10.0)
    revision_qualify.add_argument("--json", action="store_true")

    external_qualify = sub.add_parser(
        "qualify-external-provider",
        help=(
            "validate or explicitly qualify a configured HTTPS model endpoint "
            "or managed AF_UNIX gateway"
        ),
    )
    external_qualify.add_argument("--provider", choices=("http", "unix"), required=True)
    external_qualify.add_argument(
        "--endpoint",
        help="HTTPS model endpoint (required for --provider http)",
    )
    external_qualify.add_argument(
        "--allowed-host",
        action="append",
        dest="allowed_hosts",
        default=[],
        help="explicit HTTPS host[:port] allowlist entry; may be repeated",
    )
    external_qualify.add_argument(
        "--api-key-env",
        help="environment variable name containing the controller-owned HTTP secret",
    )
    external_qualify.add_argument(
        "--model-id",
        help="explicit non-secret model identity to bind to live provider responses",
    )
    external_qualify.add_argument(
        "--socket",
        dest="socket_path",
        help="managed AF_UNIX model gateway path (required for --provider unix)",
    )
    external_qualify.add_argument(
        "--managed-root",
        action="append",
        dest="managed_roots",
        default=[],
        help="private 0700 root allowed to contain --socket; may be repeated",
    )
    external_qualify.add_argument(
        "--requests",
        help=(
            "strict JSON/JSONL ProviderRequest fixture for ordinary live vector qualification; "
            "candidate workflow generates requests from runtime"
        ),
    )
    external_qualify.add_argument(
        "--candidate-context",
        "--context",
        dest="candidate_context",
        help="strict JSON candidate context (required for --candidate-workflow)",
    )
    external_qualify.add_argument(
        "--candidate-workflow",
        "--revision-workflow",
        dest="candidate_workflow",
        action="store_true",
        help="run the bounded candidate -> deterministic gate -> revision workflow",
    )
    external_qualify.add_argument("--source-generation")
    external_qualify.add_argument("--template-lock")
    external_qualify.add_argument(
        "--expected-action-kind",
        action="append",
        dest="expected_action_kinds",
        default=[],
    )
    external_qualify.add_argument(
        "--report", required=True, help="absolute qualification report path"
    )
    external_qualify.add_argument("--overwrite", action="store_true")
    external_qualify.add_argument("--timeout", type=float, default=20.0)
    external_qualify.add_argument(
        "--max-response-bytes", type=int, default=2 * 1024 * 1024
    )
    external_qualify.add_argument(
        "--live",
        action="store_true",
        help="permit one real provider qualification call; omitted means config-only validation",
    )
    external_qualify.add_argument(
        "--approved",
        action="store_true",
        help="assert the project-approved endpoint/gateway review required for live execution",
    )
    external_qualify.add_argument(
        "--approval-id", help="non-secret approval/change identifier for provenance"
    )
    external_qualify.add_argument("--json", action="store_true")

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
