"""Source loading preserves process state across entries, failures and aliases."""

import json
import shutil

import pytest

from test_assistant_loader_ic231 import DB, TOOLS, run_probe
from test_shared_loader_ic231 import installation

pytestmark = pytest.mark.skipif(not DB.is_file(), reason="IC231 is not installed")


def test_all_menu_groups_reverse_order_preserves_functions_and_classes(tmp_path):
    paths = ["scripts/sicoAutoLoad.il", "tools/common/skill++/LSF.ils",
             "tools/mtsnl/skill++/MTS.ils", "tools/utility/skill++/TECHLIB.ils",
             "tools/utility/skill++/NL2VIEW.ils"]
    paths += [f"tools/{name}/skill++/{name.upper()}.ils"
              for name in ("drc", "lvs", "rce", "lef", "ai", "sico")]
    loads = [f"load({json.dumps(str(TOOLS.parent / path))})" for path in paths]
    output = run_probe(tmp_path, [
        *loads,
        "before=SICO_sourceStatus() baseClass=findClass('INPUTGUI)",
        "beforeIpc=aiIpcState beforeLsf=cadLsfDiscoveryState",
        *reversed(loads), *loads,
        'unless(equal(before SICO_sourceStatus()) error("Unchanged file reloaded"))',
        "unless(and(eq(baseClass findClass('INPUTGUI)) eq(beforeIpc aiIpcState)"
        ' eq(beforeLsf cadLsfDiscoveryState)) error("Session state replaced"))',
    ], gui=True)
    assert "redefined" not in output.lower()
    assert "PARSER WARNING" not in output


def test_initialization_retries_only_failed_callback_and_keeps_file_status(tmp_path):
    loader = json.dumps(str(TOOLS.parent / "scripts/sicoSource.il"))
    output = run_probe(tmp_path, [
        f"load({loader})", "firstCount=0 secondCount=0 allowSecond=nil",
        "procedure(initFirst() firstCount=firstCount+1 nil)",
        'procedure(initSecond() secondCount=secondCount+1 unless(allowSecond error("init fault")) t)',
        'SICO_sourceRequire("probe" "v1" nil)',
        "when(errset(SICO_sourceInitialize(\"probe\" '(initFirst initSecond)) nil)"
        ' error("Failed initializer accepted"))',
        'unless(cadr(SICO_sourceStatus("probe"))==\'failed error("False group completion"))',
        'allowSecond=t SICO_sourceRequire("probe" "v1" nil)',
        'SICO_sourceInitialize("probe" \'(initFirst initSecond))',
        'SICO_sourceInitialize("probe" \'(initFirst initSecond))',
        'unless(and(firstCount==1 secondCount==2) error("Successful callback repeated"))',
    ], expected_error="init fault")
    assert "redefined" not in output.lower()


def test_flow_forms_reopen_after_all_shared_dependencies_loaded(tmp_path):
    lines = ['setShellEnvVar("SICO_PYTHON" "/software/pkgs/python/3.9.13/bin/python3")']
    for name in ("drc", "lvs", "rce", "lef"):
        lines.append(f'load({json.dumps(str(TOOLS / name / "skill++" / (name.upper()+".ils")))})')
    for display in ("cadDisplayDrcForm", "cadDisplayLvsForm", "cadDisplayRceForm",
                    "cadDisplayLefForm", "cadDisplayStreamGdsForm", "cadDisplayExportCdlForm"):
        lines.extend([
            f"first={display}()",
            'unless(and(first hiIsForm(first) hiIsFormDisplayed(first)) error("Form not displayed"))',
            "hiFormClose(first)",
            f"second={display}()",
            'unless(and(eq(first second) hiIsFormDisplayed(second)) error("Form not reused"))',
            "hiFormClose(second)",
        ])
    output = run_probe(tmp_path, lines, gui=True)
    assert "redefined" not in output.lower()
    assert "Bad value" not in output


def test_aliases_share_identity_and_mixed_installation_is_rejected(tmp_path):
    root = installation(tmp_path)
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    other = tmp_path / "other"
    shutil.copytree(root, other)
    entry = "scripts/sicoSource.il"
    run_probe(tmp_path, [
        f"load({json.dumps(str(root / entry))})", "before=SICO_sourceStatus()",
        f"load({json.dumps(str(alias / entry))})",
        'unless(equal(before SICO_sourceStatus()) error("Alias reloaded source"))',
        f'setShellEnvVar("SICO_HOME" {json.dumps(str(root) + "/")})',
        f"load({json.dumps(str(root / entry))})",
        'unless(equal(before SICO_sourceStatus()) error("Trailing slash reloaded source"))',
        f'setShellEnvVar("SICO_HOME" {json.dumps(str(other))})',
        f"when(errset(load({json.dumps(str(other / entry))}) nil)"
        ' error("Mixed installation accepted"))',
        'unless(equal(before SICO_sourceStatus()) error("Mixed installation altered state"))',
    ], installation=root, expected_error="conflicts")
