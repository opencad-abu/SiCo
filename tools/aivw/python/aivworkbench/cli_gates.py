"""Bounded pilot and deterministic gate command dispatch."""

from __future__ import annotations

import argparse
from .errors import AivwError
from .profiles import load_profile
from .workspace import resolve_launch_paths
from .cli_output import print_json


def run(args: argparse.Namespace) -> int:
    if args.name == "m0-e":
        if args.gate is not None:
            raise AivwError("--gate is only valid with 'aivw run m1-ai'")
        from .m0e import run_m0_e

        runner = run_m0_e
    elif args.name == "m0-s":
        if args.gate is not None:
            raise AivwError("--gate is only valid with 'aivw run m1-ai'")
        from .m0s import run_m0_s

        runner = run_m0_s
    elif args.name == "m1-ai":
        if args.gate == "golden-nominal":
            from .m1ai_golden import run_m1_ai_golden_nominal

            runner = run_m1_ai_golden_nominal
        elif args.gate == "golden-pvt":
            from .m1ai_pvt import run_m1_ai_golden_pvt

            runner = run_m1_ai_golden_pvt
        elif args.gate == "rnm":
            from .m1ai_rnm import run_m1_ai_rnm

            runner = run_m1_ai_rnm
        elif args.gate == "rnm-evidence":
            from .m1ai_rnm_evidence import run_m1_ai_rnm_evidence

            runner = run_m1_ai_rnm_evidence
        elif args.gate == "spectre-evidence":
            from .m1ai_spectre_evidence import run_m1_ai_spectre_evidence

            runner = run_m1_ai_spectre_evidence
        elif args.gate == "correlation":
            from .m1ai_correlation import run_m1_ai_correlation

            runner = run_m1_ai_correlation
        else:
            raise AivwError(
                "m1-ai is staged; select the implemented gate with "
                "'aivw run m1-ai --gate golden-nominal' or "
                "'aivw run m1-ai --gate golden-pvt', "
                "'aivw run m1-ai --gate rnm', "
                "'aivw run m1-ai --gate rnm-evidence', or "
                "'aivw run m1-ai --gate spectre-evidence', or "
                "'aivw run m1-ai --gate correlation'"
            )
    else:
        raise AssertionError(args.name)
    kwargs = {"timeout": args.timeout}
    if args.name == "m1-ai" and args.gate == "rnm":
        from .toolchain import capture_module_environment, probe_tools

        profile = load_profile(args.profile)
        environment = capture_module_environment(profile.modules)
        qualified = probe_tools(profile.tools, environment)
        xrun = next(
            (
                item.path
                for item in qualified
                if item.name == "xrun" and item.status == "PASS"
            ),
            "",
        )
        kwargs["xrun_path"] = xrun
        manifest = runner(profile, resolve_launch_paths(), **kwargs)
    elif args.name == "m1-ai" and args.gate == "rnm-evidence":
        from .m1ai_rnm_evidence import run_m1_ai_rnm_evidence
        from .toolchain import capture_module_environment, probe_tools

        if not args.rnm_source_manifest:
            raise AivwError(
                "--rnm-source-manifest is required for the rnm-evidence gate"
            )
        profile = load_profile(args.profile)
        environment = capture_module_environment(profile.modules)
        qualified = probe_tools(profile.tools, environment)
        xrun = next(
            (
                item.path
                for item in qualified
                if item.name == "xrun" and item.status == "PASS"
            ),
            "",
        )
        if not xrun:
            raise AivwError("approved Xcelium xrun path could not be qualified")
        manifest = run_m1_ai_rnm_evidence(
            profile,
            resolve_launch_paths(),
            source_manifest=args.rnm_source_manifest,
            xrun_path=xrun,
            timeout=args.timeout,
        )
    elif args.name == "m1-ai" and args.gate == "spectre-evidence":
        from .m1ai_spectre_evidence import run_m1_ai_spectre_evidence
        from .toolchain import capture_module_environment, probe_tools

        if not args.spectre_source_manifest:
            raise AivwError(
                "--spectre-source-manifest is required for the spectre-evidence gate"
            )
        profile = load_profile(args.profile)
        environment = capture_module_environment(profile.modules)
        qualified = probe_tools(profile.tools, environment)
        spectre = next(
            (
                item.path
                for item in qualified
                if item.name == "spectre" and item.status == "PASS"
            ),
            "",
        )
        ocean = next(
            (
                item.path
                for item in qualified
                if item.name == "ocean" and item.status == "PASS"
            ),
            "",
        )
        if not spectre or not ocean:
            raise AivwError("approved Spectre and OCEAN paths could not be qualified")
        manifest = run_m1_ai_spectre_evidence(
            profile,
            resolve_launch_paths(),
            source_manifest=args.spectre_source_manifest,
            spectre_path=spectre,
            ocean_path=ocean,
            environment=environment,
            timeout=args.timeout,
        )
    elif args.name == "m1-ai" and args.gate == "correlation":
        kwargs.update(
            {
                "spectre_evidence": args.spectre_evidence,
                "rnm_evidence": args.rnm_evidence,
                "policy_path": args.policy,
            }
        )
        manifest = runner(load_profile(args.profile), resolve_launch_paths(), **kwargs)
    else:
        manifest = runner(load_profile(args.profile), resolve_launch_paths(), **kwargs)
    if args.json:
        print_json(manifest)
    else:
        print(f"status: {manifest['status']}")
        print(f"run_dir: {manifest['run_dir']}")
    return 0 if manifest["status"] == "PASS" else 1


def qualify(args: argparse.Namespace) -> int:
    if args.name != "m1-ai":
        raise AssertionError(args.name)
    from .m1ai import run_m1_ai_qualification

    manifest = run_m1_ai_qualification(
        load_profile(args.profile), resolve_launch_paths(), timeout=args.timeout
    )
    if args.json:
        print_json(manifest)
    else:
        print(f"status: {manifest['status']}")
        print(f"run_dir: {manifest['run_dir']}")
        print(f"next_gate: {manifest['next_gate']}")
    return 0 if manifest["status"] == "PASS" else 1
