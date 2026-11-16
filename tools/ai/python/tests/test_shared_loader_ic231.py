"""Shared loader readiness, upgrade and failure recovery in isolated Cadence processes."""

import json
from pathlib import Path
import shlex
import shutil
import sys

import pytest

from test_assistant_loader_ic231 import DB, LOADERS, TOOLS, run_probe

sys.path.insert(0, str(TOOLS))
from utility.skill_loader import render as render_group
from utility.skill_loader_contract import contract as group_contract, manifest

MANIFEST = Path("tools/utility/ai_shared_sources.json")
OUTPUT = Path("tools/ai/skill++/AI_shared.ils")

def render(root):
    return render_group(root, "ai_shared")

def contract(root):
    fingerprint, rows = group_contract(root, manifest(root)["ai_shared"])
    return fingerprint, [(path, exports, revisions) for path, _, exports, revisions, _ in rows]


pytestmark = pytest.mark.skipif(not DB.is_file(), reason="IC231 dbAccess is not installed")


def installation(tmp_path):
    root = tmp_path / "installation"
    for directory in ("tools/ai/skill", "tools/ai/skill++", "tools/sico/skill",
                      "tools/sico/skill++", "tools/common/skill", "etc/config"):
        shutil.copytree(TOOLS.parent / directory, root / directory)
    shutil.copytree(TOOLS.parent / "scripts", root / "scripts")
    shutil.copytree(TOOLS.parent / "tools/utility/skill_loader", root / "tools/utility/skill_loader")
    # Render the AI group in a deliberately minimal test installation.
    full = json.loads((root / "tools/utility/skill_loader/manifest.json").read_text())
    full["groups"] = {key: value for key, value in full["groups"].items()
                      if key in {"core", "ai_shared", "assistant", "sico"}}
    full.pop("source_directories")
    full.pop("excluded")
    (root / "tools/utility/skill_loader/manifest.json").write_text(json.dumps(full))
    (root / "bin").mkdir()
    (root / MANIFEST).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(TOOLS.parent / MANIFEST, root / MANIFEST)
    return root


def snapshot():
    _, rows = contract(TOOLS.parent)
    exports = " ".join(name for _, names, _ in rows for name in names)
    return [
        f"loaderExports='({exports})",
        "loaderFunctions=foreach(mapcar name loaderExports list(name getd(name)))",
        "loaderTables=list(aiWindowEntries aiPdkTransfers aiCreatePrepares aiCrExtensions)",
        "foreach(table loaderTables table['eq04Probe]=list(\"preserved\"))",
    ]


def stable():
    return [
        'unless(forall(pair loaderFunctions eq(cadr(pair) getd(car(pair))))'
        ' error("Shared function identity changed"))',
        'unless(and(eq(car(loaderTables) aiWindowEntries)'
        ' eq(cadr(loaderTables) aiPdkTransfers) eq(caddr(loaderTables) aiCreatePrepares)'
        ' eq(cadddr(loaderTables) aiCrExtensions)'
        ' forall(table loaderTables table[\'eq04Probe]==list("preserved")))'
        ' error("Shared runtime state changed"))',
    ]


@pytest.mark.parametrize("order", [("assistant", "assistant"), ("copilot", "copilot"),
                                   ("assistant", "copilot"), ("copilot", "assistant")])
def test_all_shared_exports_and_state_survive_entry_orders(tmp_path, order):
    loads = [f"load({json.dumps(str(LOADERS[key]))})" for key in order]
    output = run_probe(tmp_path, [loads[0], *snapshot(), loads[1], *stable(),
                                  *loads, *loads, *stable()])
    assert "redefined" not in output.lower()


@pytest.mark.parametrize("fault", ["legacy_slots", "missing_wire", "old_revision", "broken_revision",
                                   "missing_guarded_export", "old_guarded_revision"])
def test_existing_process_repairs_old_or_missing_shared_members(tmp_path, fault):
    loader = json.dumps(str(TOOLS.parent / OUTPUT))
    legacy = tmp_path / "legacy.ils"
    legacy.write_text('aiSharedLoaderVersion="20261009.ai.shared.v3"\n'
                      'set(\'aiSharedLoaderVersion "20261009.ai.shared.v3")\n')
    changes = {
        "legacy_slots": [f"load({json.dumps(str(legacy))})"],
        "missing_wire": ["putd('aiPdkSymbolWireProbe nil)"],
        "old_revision": ['putd(\'aiTemplatesRevision lambda(() "old"))'],
        "broken_revision": ['putd(\'aiTemplatesRevision lambda(() error("old broken revision")))'],
        "missing_guarded_export": ["putd('aiJsonEscape nil)"],
        "old_guarded_revision": ['putd(\'aiInspectRevision lambda(() "old"))'],
    }
    output = run_probe(tmp_path, [
        f"load({loader})", "loaderTransfers=aiPdkTransfers",
        "aiPdkTransfers['eq04Probe]=list(\"preserved\")", *changes[fault],
        f"load({loader})",
        'unless(and(isCallable(\'aiPdkSymbolWireProbe)'
        ' aiTemplatesRevision()=="20261009.ai.templates.v18"'
        ' isCallable(\'aiJsonEscape) aiInspectRevision()=="20261101.ai.inspect.v12"'
        ' eq(loaderTransfers aiPdkTransfers) aiPdkTransfers[\'eq04Probe]==list("preserved"))'
        ' error("Upgrade lost state or failed to repair shared members"))',
        *snapshot(), 'printf("UPGRADE_COMPLETE\\n")', f"load({loader})", *stable(),
    ])
    assert "redefined" not in output.lower()


@pytest.mark.parametrize("fault", ["missing_early", "missing_late", "wrong_revision", "missing_export"])
def test_partial_load_never_publishes_completion_and_retries(tmp_path, fault):
    root = installation(tmp_path)
    member = {"missing_early": "AI_protocol.il", "missing_late": "AI_maestro_ac.il",
              "wrong_revision": "AI_templates.il", "missing_export": "AI_pdk_symbol_wire.il"}[fault]
    target = root / "tools/ai/skill" / member
    held = target.with_suffix(".held")
    target.rename(held)
    if fault == "wrong_revision":
        target.write_text(held.read_text().replace("20261009.ai.templates.v18", "wrong"))
    elif fault == "missing_export":
        target.write_text(held.read_text().replace("aiPdkSymbolWireProbe(", "eq04WrongExport("))
    loader = json.dumps(str(root / OUTPUT))
    restore = json.dumps(f"cp {shlex.quote(str(held))} {shlex.quote(str(target))}")
    output = run_probe(tmp_path, [
        # A previous successful process must not retain its old completed marker.
        'set(\'aiSharedLoaderVersion "20261009.ai.shared.v3")',
        f'when(errset(load({loader}) nil) error("Incomplete shared load accepted"))',
        'when(aiSharedLoaderVersion error("False shared completion"))',
        "beforeRetry=SICO_sourceStatus()",
        f'unless(system({restore})==0 error("Restore failed"))', f"load({loader})",
        "unless(forall(row beforeRetry or(cadr(row)!='loaded"
        ' nth(3 assoc(car(row) SICO_sourceStatus()))==nth(3 row)))'
        ' error("Retry reloaded a successful member"))',
        'unless(aiSharedLoaderVersion error("Retry did not complete"))',
        *snapshot(), 'printf("RETRY_COMPLETE\\n")', f"load({loader})", *stable(),
    ], installation=root, expected_error=(
        "load: can't access file" if fault.startswith("missing_") and fault != "missing_export"
        else "SiCo source contract failed"))
    assert "redefined" not in output.split("RETRY_COMPLETE")[-1].lower()


def test_new_unversioned_member_invalidates_old_completed_process(tmp_path):
    root = installation(tmp_path)
    before = root / OUTPUT
    held = before.with_suffix(".before")
    shutil.copy2(before, held)
    data = json.loads((root / MANIFEST).read_text())
    data["sources"].append("AI_eq04_probe.il")
    (root / MANIFEST).write_text(json.dumps(data))
    (root / "tools/ai/skill/AI_eq04_probe.il").write_text("procedure(eq04NewMember() t)\n")
    updated = before.with_suffix(".after")
    updated.write_text(render(root))
    # Keep the real filename/anchor: overwrite it only after the old loader succeeds.
    replace = json.dumps(f"cp {shlex.quote(str(updated))} {shlex.quote(str(before))}")
    loader = json.dumps(str(before))
    output = run_probe(tmp_path, [
        f"load({loader})", "loaderOldVersion=aiSharedLoaderVersion",
        "loaderTransfers=aiPdkTransfers", *snapshot(),
        f'unless(system({replace})==0 error("Upgrade copy failed"))', f"load({loader})",
        'unless(and(loaderOldVersion!=aiSharedLoaderVersion isCallable(\'eq04NewMember)'
        ' eq(loaderTransfers aiPdkTransfers)) error("New member was skipped"))',
        *stable(), 'printf("UPGRADE_COMPLETE\\n")', f"load({loader})", *stable(),
    ], installation=root)
    assert "redefined" not in output.lower()


def test_source_fingerprint_refreshes_guarded_fragment_without_revision_bump(tmp_path):
    root = installation(tmp_path)
    loader = json.dumps(str(root / OUTPUT))
    source = root / "tools/ai/skill/AI_inspect_values.il"
    source.write_text(source.read_text().replace("procedure(aiInspectMaxItems(value)",
                                               "procedure(aiInspectMaxItems(value)\n  17\n)\n"
                                               "procedure(eq04OriginalMaxItems(value)"))
    updated = (root / OUTPUT).with_suffix(".after")
    updated.write_text(render(root))
    # Before upgrade the loader still represents the old contract and does not
    # know the new function; make its first load use the original source.
    held = source.with_suffix(".after")
    shutil.copy2(source, held)
    shutil.copy2(TOOLS / "ai/skill/AI_inspect_values.il", source)
    commands = [f"cp {shlex.quote(str(a))} {shlex.quote(str(b))}" for a, b in
                [(held, source), (updated, root / OUTPUT)]]
    output = run_probe(tmp_path, [
        f"load({loader})", 'unless(aiInspectMaxItems(200)==200 error("Initial source changed"))',
        "beforeUpdate=SICO_sourceStatus()",
        *[f'unless(system({json.dumps(cmd)})==0 error("Upgrade copy failed"))' for cmd in commands],
        f"load({loader})", 'unless(aiInspectMaxItems(200)==17 error("Local guard skipped new source"))',
        'unless(forall(row beforeUpdate'
        ' or(car(row)=="tools/ai/skill/AI_inspect_values.il"'
        ' nth(3 assoc(car(row) SICO_sourceStatus()))==nth(3 row)))'
        ' error("Update reloaded an unchanged member"))',
        *snapshot(), 'printf("UPGRADE_COMPLETE\\n")', f"load({loader})", *stable(),
    ], installation=root)
    assert "redefined" not in output.lower()
