"""Guard the public boundaries shared by cadai circuit domains."""

import ast
from pathlib import Path


CADAI = Path(__file__).resolve().parents[1] / "cadai"


def _private_imports(path: Path, module: str) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.level == 1
        and node.module == module
        for alias in node.names
        if alias.name.startswith("_")
    ]


def test_waveform_and_simulation_consumers_use_domain_operations() -> None:
    assert _private_imports(CADAI / "ac_tools.py", "waveform_tools") == []
    assert _private_imports(CADAI / "cdf_update.py", "simulation_recipe") == []
    assert _private_imports(CADAI / "project_tools.py", "simulation_recipe") == []


def test_domain_operations_have_intentional_names() -> None:
    from cadai.simulation_recipe import call_simulation_native
    from cadai.waveform_tools import capture_waveform

    assert callable(call_simulation_native)
    assert callable(capture_waveform)
