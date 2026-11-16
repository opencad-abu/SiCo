"""Current operation docs must agree with code-owned versions and defaults."""

from pathlib import Path

from utility import documentation_contract


def test_current_documentation_matches_runtime_facts():
    result = documentation_contract.check(Path(__file__).resolve().parents[3])
    assert result["status"] == "passed", result["errors"]
    assert result["version"] == "0.162.1"


def test_contract_rejects_stale_current_text(tmp_path):
    root = tmp_path / "repo"
    (root / "tools/ai/python/cadai").mkdir(parents=True)
    (root / "tools/ai/python/cadai/codex_selection.py").write_text(
        'CODEX_VERSION = "9.9.9"\n')
    (root / "tools/sico/python/sico/codex").mkdir(parents=True)
    (root / "tools/sico/python/sico/codex/knowledge_runtime.py").write_text(
        '"default_tools_approval_mode": "prompt"\n')
    for relative in documentation_contract.CURRENT_PATHS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("CODEX_VERSION 9.9.9\n" if "CURRENT_DOCUMENTATION" in relative
                        else "placeholder\n")
    (root / "docs/ENVIRONMENT.md").write_text(
        "知识工具仍需当前回合的原生审批\n")
    result = documentation_contract.check(root)
    assert not result["passed"]
    assert any("approval" in error for error in result["errors"])
    assert any("0.162.1" in error or "9.9.9" in error or "absent" in error
               for error in result["errors"])
