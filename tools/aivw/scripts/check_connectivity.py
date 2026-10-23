"""Offline structural connectivity checker for an official SI inventory.

This command is intentionally small and deterministic.  It is useful for
debugging or CI with a previously captured payload; formal AIVW runs should
prefer the ``connectivity.check`` executor so manifests and gate state are
recorded automatically.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))


def _validate_production_interpreter() -> dict[str, object] | None:
    """Require the explicit CAD_PYTHON contract before doing any work."""
    from aivworkbench.runtime_info import validate_production_runtime

    try:
        return validate_production_runtime(require_environment=True)
    except RuntimeError as exc:
        print(json.dumps({"status": "BLOCKED_ENVIRONMENT", "error": str(exc)}, sort_keys=True))
        return None

def main(argv: list[str] | None = None) -> int:
    if _validate_production_interpreter() is None:
        return 2
    from aivworkbench.connectivity import (
        Structure,
        compare_structures,
        parse_globalmap,
        parse_globalmap_models,
        parse_si_map,
        parse_si_netlists,
        parse_verilog,
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--netlist", action="append", required=True, help="official SI netlist file (repeatable)")
    parser.add_argument("--map", dest="map_path", required=True)
    parser.add_argument("--globalmap", required=True)
    parser.add_argument("--candidate", required=True, help="candidate structural SV")
    parser.add_argument("--module", required=True)
    parser.add_argument("--port", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        aliases = parse_si_map(Path(args.map_path).read_text(encoding="utf-8"))
        globalmap_text = Path(args.globalmap).read_text(encoding="utf-8")
        globals_ = parse_globalmap(globalmap_text)
        model_bindings = parse_globalmap_models(globalmap_text)
        if not set(globals_).issubset(set(aliases)):
            raise ValueError("globalmap aliases are absent from map")
        official = Structure(
            parse_si_netlists(Path(item).read_text(encoding="utf-8") for item in args.netlist).modules,
            globals=globals_, aliases=aliases,
        )
        if not {module.name for module in official.modules}.issubset({module for _view, module in model_bindings}):
            raise ValueError("globalmap model bindings omit an SI module")
        candidate = Structure(parse_verilog(Path(args.candidate).read_text(encoding="utf-8")), globals=globals_)
        findings = compare_structures(
            official, candidate, expected_module=args.module,
            expected_ports=tuple(args.port) or None, expected_globals=globals_,
        )
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "BLOCKED_INPUT", "error": str(exc)}, indent=2))
        return 2
    payload = {"status": "PASS" if not findings else "FAIL_CONNECTIVITY", "findings": [item.as_dict() for item in findings]}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not findings else 1


if __name__ == "__main__":
    raise SystemExit(main())
