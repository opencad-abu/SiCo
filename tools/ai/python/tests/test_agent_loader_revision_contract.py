"""AI entries delegate membership and revisions to the generated source contract."""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from utility.skill_loader import check
from utility.skill_loader_contract import contract, manifest, members


def test_entries_are_current_and_shared_members_have_one_owner():
    assert check(ROOT.parent) == []
    groups = manifest(ROOT.parent)
    paths = [path for key in ("ai_shared", "assistant", "sico")
             for path in members(ROOT.parent, groups[key])]
    assert len(paths) == len(set(paths))
    _, rows = contract(ROOT.parent, groups["ai_shared"])
    entries = {Path(path).name: (exports, revisions) for path, _, exports, revisions, _ in rows}
    assert "aiPdkSymbolWireProbe" in entries["AI_pdk_symbol_wire.il"][0]
    assert entries["AI_pdk_symbol_wire.il"][1]
    assert "aiTestWorkspaceLibrary" in entries["AI_test_workspace.il"][0]


def test_installation_guard_matches_published_version():
    source = (ROOT / "common/skill/SICO_installation.il").read_text()
    expected = re.search(r'sicoInstallationVersion=="([^"]+)"', source)[1]
    published = re.findall(r'sicoInstallationVersion="([^"]+)"', source)
    assert published == [expected]


def test_sico_launcher_registers_nonblocking_virtuoso_exit_cleanup():
    source = (ROOT / "sico/skill/SICO_launcher.il").read_text()
    assert "regExitBefore('sicoExitCleanup)" in source
    request_start = source.index("procedure(sicoRequestShutdown(cid)")
    request_end = source.index("procedure(sicoExitCleanup()")
    request = source[request_start:request_end]
    start = source.index("procedure(sicoExitCleanup()")
    end = source.index("procedure(sicoRegisterExitCleanup()")
    cleanup = source[start:end]
    assert "ipcWriteProcess(cid \"@shutdown\\n\")" in request
    assert "ipcWait" not in request
    assert "ipcCloseProcess" not in request
    assert "ipcWait" not in cleanup
    assert "ipcCloseProcess" not in cleanup


def test_quick_composer_registers_nonblocking_virtuoso_exit_cleanup():
    source = (ROOT / "sico/skill/SICO_quick.il").read_text()
    assert "regExitBefore('sicoQuickExitCleanup)" in source
    start = source.index("procedure(sicoQuickExitCleanup()")
    end = source.index("procedure(sicoQuickRegisterExitCleanup()")
    cleanup = source[start:end]
    assert "ipcSignalProcess(child 'TERM)" in cleanup
    assert "ipcWriteProcess" not in cleanup
    assert "ipcWait" not in cleanup
    assert "ipcCloseProcess" not in cleanup


def test_relay_stderr_does_not_retain_transient_ipc_callback_data():
    source = (ROOT / "sico/skill/SICO_context.il").read_text()
    start = source.index("procedure(sicoError(")
    end = source.index("procedure(sicoExit(", start)
    error = source[start:end]
    assert 'warn("' not in error
    assert 'printf("*WARNING* Silicon Copilot Relay: %s" data)' in error


def test_assistant_load_compares_canonical_workspace_paths():
    source = (ROOT / "sico/skill/SICO_context.il").read_text()
    assert "aarPath=simplifyFilename(aiCopilotHex(aarEncoded))" in source
    assert (
        "aarCwd=simplifyFilename(or(aarTarget['cwd] aiWorkingDirectory()))"
        in source
    )
    # A preserved /workarea alias would not match the canonicalized aarPath.
    assert "aiWorkingDirectory()) t)" not in source
