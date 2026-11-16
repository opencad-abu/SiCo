"""Source-test adapter for the authoritative private boundary manifest."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[4]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from utility.boundary_objects import composition_errors, object_errors
from utility.boundary_rules import violations as source_violations
from utility.check_private_import_boundaries import check_rule, load_manifest


def contract(identifier, kind="rules"):
    return next(item for item in load_manifest()[kind] if item["id"] == identifier)


def violations(source, path="tools/sico/python/sico_ui/window.py"):
    return source_violations(source, contract("assistant-ui-backend"), path=path,
                             module="sico_ui." + Path(path).stem)


def production_violations(identifier):
    return check_rule(contract(identifier))


def product_errors(value):
    return object_errors(value, contract("frontend-" + type(value).__name__, "objects"))


def window_errors(value):
    return composition_errors(value, contract("assistant-window-composition", "compositions"))
