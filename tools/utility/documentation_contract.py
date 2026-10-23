"""Lightweight checks for current-vs-historical SiCo documentation facts."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CURRENT_PATHS = (
    "docs/ENVIRONMENT.md",
    "docs/SICO_SOURCE_TESTS.md",
    "docs/SICO_DIAGNOSTICS.md",
    "docs/SICO_RESOURCE_BUDGETS.md",
    "docs/SICO_BACKEND_SUPPORT.md",
    "docs/SICO_CURRENT_DOCUMENTATION.md",
    "docs/SICO_REFACTOR_STATUS.md",
    "tools/ai/README.md",
    "tools/sico/python/sico/README.md",
    "tools/ai/docs/COPILOT_STUDIO_ARCHITECTURE.md",
    "tools/ai/docs/AI_ASSISTANT_AGENT_QUICKSTART.md",
    "tools/ai/docs/COPILOT_STUDIO_AGENT_SERVICE_OPERATIONS.md",
    "tools/ai/docs/SILICON_COPILOT_EXTERNAL_RESOURCES.md",
    "tools/ai/terminal/README.md",
    "tools/ai/terminal/pyqt/README.md",
    "tools/aivw/docs/ARCHITECTURE.md",
)

RETIRED_MARKERS = (
    "0.153.4",
    "0.154.0",
    "0.156.1",
    "CAD_PYTHON",
    "CAD_TEMP_DIR",
    ".cad/ai",
)

HISTORICAL_WORDS = ("历史", "当时", "旧", "迁移", "退役", "legacy", "historical",
                    "retired")


def _version(root):
    source = (root / "tools/ai/python/cadai/codex_selection.py").read_text()
    match = re.search(r'^CODEX_VERSION\s*=\s*"([^"]+)"', source, re.MULTILINE)
    return match.group(1) if match else None


def _links(text):
    return re.findall(r"\[[^\]]+\]\(([^)]+)\)", text)


def _historical(line):
    return any(word in line.lower() for word in HISTORICAL_WORDS)


def check(root=ROOT):
    root = Path(root)
    errors = []
    version = _version(root)
    if not version:
        errors.append("current Codex version is not declared")
    documents = {}
    for relative in CURRENT_PATHS:
        path = root / relative
        if not path.is_file():
            errors.append("missing current document: " + relative)
            continue
        documents[relative] = path.read_text()

    for relative in (
        "docs/SICO_CURRENT_DOCUMENTATION.md",
        "tools/ai/README.md",
        "tools/sico/python/sico/README.md",
        "tools/ai/docs/COPILOT_STUDIO_ARCHITECTURE.md",
        "tools/ai/docs/AI_ASSISTANT_AGENT_QUICKSTART.md",
        "tools/ai/docs/SILICON_COPILOT_EXTERNAL_RESOURCES.md",
        "tools/aivw/docs/ARCHITECTURE.md",
    ):
        text = documents.get(relative, "")
        if version and version not in text:
            errors.append(f"{relative}: current Codex version {version} is absent")

    for relative in (
        "tools/ai/README.md",
        "tools/sico/python/sico/README.md",
        "tools/ai/docs/COPILOT_STUDIO_ARCHITECTURE.md",
        "tools/ai/docs/AI_ASSISTANT_AGENT_QUICKSTART.md",
        "tools/ai/docs/COPILOT_STUDIO_AGENT_SERVICE_OPERATIONS.md",
        "tools/ai/docs/SILICON_COPILOT_EXTERNAL_RESOURCES.md",
        "tools/ai/terminal/README.md",
        "tools/ai/terminal/pyqt/README.md",
        "tools/aivw/docs/ARCHITECTURE.md",
    ):
        for number, line in enumerate(documents.get(relative, "").splitlines(), 1):
            if _historical(line):
                continue
            for marker in RETIRED_MARKERS:
                if marker in line:
                    errors.append(f"{relative}:{number}: retired term is not marked historical: {marker}")

    environment = documents.get("docs/ENVIRONMENT.md", "")
    if "知识工具仍需当前回合的原生审批" in environment:
        errors.append("docs/ENVIRONMENT.md still claims per-call knowledge approval")
    if "宿主审批默认为 `approve`" not in environment or "不逐次弹窗" not in environment:
        errors.append("docs/ENVIRONMENT.md does not state the current knowledge approval default")
    runtime = (root / "tools/sico/python/sico/codex/knowledge_runtime.py").read_text()
    if '"default_tools_approval_mode": "approve"' not in runtime:
        errors.append("knowledge runtime approval default no longer matches the document")

    index = documents.get("docs/SICO_CURRENT_DOCUMENTATION.md", "")
    for target in _links(index):
        if "://" in target or target.startswith("#"):
            continue
        target = target.split("#", 1)[0]
        if not (root / "docs" / target).exists():
            errors.append("current documentation link is missing: " + target)
    for relative in (
        "docs/ENVIRONMENT.md",
        "docs/SICO_SOURCE_TESTS.md",
        "docs/SICO_DIAGNOSTICS.md",
        "docs/SICO_RESOURCE_BUDGETS.md",
        "docs/SICO_BACKEND_SUPPORT.md",
    ):
        if Path(relative).name not in index and relative not in index:
            errors.append("current documentation index omits: " + relative)
    return {"status": "passed" if not errors else "failed",
            "passed": not errors, "version": version, "errors": errors}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    result = check(args.root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
