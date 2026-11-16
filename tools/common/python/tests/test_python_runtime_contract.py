"""Production runtime selection derives only from the SiCo installation."""

from pathlib import Path


TOOLS = Path(__file__).resolve().parents[3]
FORBIDDEN = ("/software/pkgs/python", "/software/sico/", "/App/others/", "SiCoEDA-v")
SUFFIXES = {".py", ".il", ".ils", ".sh"}


def test_production_sources_have_no_hardcoded_python_runtime():
    offenders = []
    for path in TOOLS.rglob("*"):
        if path.suffix not in SUFFIXES or not path.is_file():
            continue
        parts = set(path.relative_to(TOOLS).parts)
        if "tests" in parts or "docs" in parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for literal in FORBIDDEN:
            if literal in text:
                offenders.append(f"{path.relative_to(TOOLS)}: {literal}")
    assert offenders == []
