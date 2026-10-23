"""Apply the native builder's source closure contract in the engineering gate."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.python_native_spec import check_closure, load_spec
from deploy.runtime_records import relative


def check_inventories(root: Path, payload):
    errors, reports = [], []
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        return ["unsupported closure manifest schema"], []
    specs = payload.get("inventories")
    if (not isinstance(specs, list) or not specs
            or not all(isinstance(value, str) and value for value in specs)
            or len(specs) != len(set(specs))):
        return ["inventories must be a non-empty unique path list"], []
    for value in specs:
        try:
            path = root / relative(value)
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("native inventory outside repository")
            source_root, data = load_spec(path)
            if not source_root.is_relative_to(root.resolve()):
                raise ValueError("native source root outside repository")
            report = dict(inventory=value, **check_closure(source_root, data))
            reports.append(report)
            errors.extend(f"{value}: {issue}" for issue in report["errors"])
        except (OSError, ValueError, SyntaxError, KeyError, TypeError, ImportError) as exc:
            errors.append(f"{value}: closure check failed: {exc}")
    return errors, reports
