"""Commands that inspect launch configuration and tool availability."""

from __future__ import annotations

import argparse
from . import COMMAND_NAME, PRODUCT_NAME, __version__
from .doctor import run_doctor
from .profiles import available_profiles, load_profile, product_root
from .toolchain import parse_setup_exports
from .workspace import resolve_launch_paths, resolve_project_db_root
from .cli_output import print_json


def runtime_info(args: argparse.Namespace) -> int:
    from .agent.runtime_info import collect_runtime_info, validate_production_runtime

    try:
        payload = validate_production_runtime()
    except RuntimeError as exc:
        # Keep diagnostics useful while preserving the launcher's fail-closed
        # behavior when CAD_PYTHON is absent or unsafe.
        payload = collect_runtime_info()
        payload["error"] = str(exc)
        if args.json:
            print_json(payload)
        else:
            print("error: %s" % exc)
        return 2
    if args.json:
        print_json(payload)
    else:
        for key, value in payload.items():
            print("%s: %s" % (key, value))
    return 0


def info(args: argparse.Namespace) -> int:
    profile = load_profile(args.profile)
    launch = resolve_launch_paths()
    payload_root = None
    if profile.storage:
        exports = parse_setup_exports(
            profile.setup_script, (profile.storage.payload_env,)
        )
        payload_root = resolve_project_db_root(
            exports[profile.storage.payload_env],
            launch,
            forbidden_roots=(product_root(),),
        )
    payload = {
        "product": PRODUCT_NAME,
        "command": COMMAND_NAME,
        "version": __version__,
        "profile": profile.name,
        "workspace_root": str(profile.workspace_root),
        "logical_cwd": str(launch.logical_cwd),
        "physical_cwd": str(launch.physical_cwd),
        "artifact_root": str(launch.logical_artifact_root),
        "artifact_root_physical": str(launch.physical_artifact_root),
        "control_root": str(launch.logical_artifact_root),
        "project_payload_root": str(payload_root) if payload_root else None,
        "payload_layout": profile.storage.target_layout if profile.storage else None,
    }
    if args.json:
        print_json(payload)
    else:
        for key, value in payload.items():
            print(f"{key}: {value}")
    return 0


def profiles(args: argparse.Namespace) -> int:
    values = available_profiles()
    if args.json:
        print_json({"profiles": list(values)})
    else:
        print("\n".join(values))
    return 0


def pilot(args: argparse.Namespace) -> int:
    pilot = load_profile(args.profile).pilots[args.name]
    payload = {
        "name": pilot.name,
        "title": pilot.title,
        "purpose": pilot.purpose,
        "entry": pilot.entry,
        "proves": list(pilot.proves),
        "does_not_prove": list(pilot.does_not_prove),
    }
    if args.json:
        print_json(payload)
    else:
        print(f"{pilot.name}: {pilot.title}")
        print(f"purpose: {pilot.purpose}")
        print(f"entry: {pilot.entry}")
        print("proves:")
        for value in pilot.proves:
            print(f"  - {value}")
        print("does not prove:")
        for value in pilot.does_not_prove:
            print(f"  - {value}")
    return 0


def doctor(args: argparse.Namespace) -> int:
    manifest = run_doctor(load_profile(args.profile), resolve_launch_paths())
    if args.json:
        print_json(manifest)
    else:
        print(f"status: {manifest['status']}")
        print(f"run_dir: {manifest['run_dir']}")
        for tool in manifest["tools"]:
            print(f"tool {tool['name']}: {tool['status']} {tool['path']}")
    return 0 if manifest["status"] == "PASS" else 1
