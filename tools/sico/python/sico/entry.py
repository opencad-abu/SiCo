"""Bootstrap the primary command from its fixed location in a SiCo installation."""

from pathlib import Path
import sys


def main(argv=None):
    root = Path(__file__).resolve().parents[4]
    common = root / "tools/common/python"
    sys.path.insert(0, str(common))
    from sicopaths import installation

    selected = installation(anchor=root)
    sys.path[:0] = [str(selected.tool("sico") / "python"),
                   str(selected.tool("ai") / "python")]
    from .interpreter import ensure_cad_python

    ensure_cad_python(selected.tool("sico") / "python/sico-entry")
    from .cli import main as run

    return run(argv)
