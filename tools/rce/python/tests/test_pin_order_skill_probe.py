"""Exercise pin-order defaults, transitions, persistence and launch validation in Virtuoso."""
from __future__ import annotations

import os
from pathlib import Path
import re

import pytest

from test_absolute_path_skill_probe import _install_tree, _run_probe, _write


@pytest.mark.skipif(
    os.environ.get("RCE_RUN_SKILL_PROBE") != "1", reason="set RCE_RUN_SKILL_PROBE=1"
)
def test_pin_order_form_defaults_sources_validation_and_profile(tmp_path: Path) -> None:
    install = _install_tree(tmp_path)
    launch = tmp_path / "launch"
    _write(launch / "cds.lib")
    pins = _write(launch / "pins.cdl", ".subckt top Z A\n.ends\n")
    _write(launch / "tech/Typ/qrcTechFile")
    _write(launch / "tech/Typ/nxtgrd")
    _write(launch / "tech/Typ/tran.map")
    _write(launch / "tech/Typ/xrc.cal")

    output = _run_probe(
        tmp_path, flow="RCE", entry=f"{install}/tools/rce/skill++/RCE.ils",
        commands=(
            "cadDisplayRceForm()",
            "form=rceForm",
            'unless(and(form~>pinOrderBtn~>value form~>pinOrderType~>value=="CDL Netlist File") error("Missing default pin order"))',
            # Updating the loader must rebuild forms from the previous revision.
            'form~>pinOrderBtn~>value=nil',
            'putpropq(form "20260908.recognize.gates.v1" rceReductionFormVersion)',
            'putd(\'rceFrontendRevision lambda(() "20260908.recognize.gates.v1"))',
            f'load("{install}/tools/rce/skill++/RCE.ils")',
            'cadDisplayRceForm() form=rceForm',
            'unless(and(form~>pinOrderBtn~>value form~>pinOrderType~>value=="CDL Netlist File") error("Reload did not rebuild stale pin controls"))',
            "savedMessage=getd('GUI_messageApp)",
            "pinMessages=nil",
            "putd('GUI_messageApp lambda((severity title message) when(severity=='e pinMessages=cons(message pinMessages)) t))",
            # A real form is used, while dialog presentation is captured to avoid
            # waiting for a human to close deliberate validation errors.
            'foreach(inputType list("SVDB" "CCI")',
            '  form~>inpType~>value=inputType rceInpCB(form)',
            '  unless(and(form~>pinOrderBtn~>value form~>pinOrderType~>items==list("User Defined File") form~>pinOrderType~>value=="User Defined File" form~>pinOrderFile~>enabled) error("Invalid database-input pin sources"))',
            ')',
            'form~>pinOrderFile~>value="missing.cdl"',
            'pinMessages=nil',
            'rcePinOrderFileCB(form)',
            'unless(pinMessages error("Missing-file field callback did not show a dialog"))',
            # Stop before even dispatching a single/batch job or allocating config.
            "savedDispatch=getd('cadBatchDispatchStart)",
            "pinDispatched=nil",
            "putd('cadBatchDispatchStart lambda((f a b) pinDispatched=t))",
            "pinMessages=nil",
            "rceRunCB(form)",
            "rceRunCloseCB(form)",
            'when(pinDispatched error("Invalid pin file reached extraction dispatch"))',
            'unless(and(pinMessages hiIsFormDisplayed(form)) error("Run rejection failed"))',
            'when(rcePrint(form) error("Invalid pin file produced a run config"))',
            "putd('cadBatchDispatchStart savedDispatch)",
            'form~>pinOrderFile~>value=""',
            'when(rceValidatePinOrder(form) error("Empty file accepted"))',
            'form~>pinOrderFile~>value="tech"',
            'when(rceValidatePinOrder(form) error("Directory accepted as pin file"))',
            f'form~>pinOrderFile~>value="{pins}"',
            'unless(rceValidatePinOrder(form) error("Existing pin file rejected"))',
            'form~>inpType~>value="CDL+GDS" rceInpCB(form)',
            'unless(length(form~>pinOrderType~>items)==2 error("Source CDL choice not restored"))',
            'form~>pinOrderType~>value="CDL Netlist File" rcePinOrderTypeCB(form)',
            'form~>pinOrderType~>value="User Defined File" rcePinOrderTypeCB(form)',
            f'unless(form~>pinOrderFile~>value=="{pins}" error("Source switch lost the user pin file"))',
            # Temporarily unsupported output restores both enabled and disabled intent.
            'form~>extTool~>value="StarRC"',
            'form~>outType~>value="spef" rceSyncNetlistCustomize(form)',
            'when(form~>pinOrderBtn~>value error("SPEF pin order stayed enabled"))',
            'form~>outType~>value="dspf" rceSyncNetlistCustomize(form)',
            f'unless(and(form~>pinOrderBtn~>value form~>pinOrderFile~>value=="{pins}") error("Pin choice lost across output transition"))',
            'form~>pinOrderBtn~>value=nil rcePinOrderBtnCB(form)',
            'form~>outType~>value="spef" rceSyncNetlistCustomize(form)',
            'form~>outType~>value="dspf" rceSyncNetlistCustomize(form)',
            'when(form~>pinOrderBtn~>value error("Explicit disabled choice lost"))',
            'form~>extTool~>value="QRC" form~>nameSource~>value="Schematic"',
            'rceRestorePinOrder(form t "CDL Netlist File" "")',
            'form~>nameSource~>value="Layout" rceSyncNetlistCustomize(form)',
            'when(form~>pinOrderBtn~>value error("Layout QRC pin order enabled"))',
            'form~>nameSource~>value="Schematic" rceSyncNetlistCustomize(form)',
            'unless(form~>pinOrderBtn~>value error("Default not restored after QRC namespace switch"))',
            # Real profile apply must preserve false and default omitted fields to true.
            'form~>corner~>value="Typ"',
            f'data=list(list("input.type" "CDL+GDS") list("extract.tool" "QRC") list("extract.output_type" "dspf") list("extract.corner" "Typ") list("extract.tech_dir" "{launch / "tech/Typ"}") list("netlist.pin_order_enable" nil))',
            'unless(rceProfileApply(form data) error("Profile apply failed"))',
            'when(form~>pinOrderBtn~>value error("Profile false ignored"))',
            'data=reverse(cdr(reverse(data)))',
            'unless(rceProfileApply(form data) error("Default profile apply failed"))',
            'unless(and(form~>pinOrderBtn~>value form~>pinOrderType~>value=="CDL Netlist File") error("Profile missing-key default not enabled"))',
            # Replacing a profile must not resurrect the suspended previous choice.
            'form~>nameSource~>value="Layout" rceSyncNetlistCustomize(form)',
            'data=append(data list(list("netlist.pin_order_enable" nil)))',
            'unless(rceProfileApply(form data) error("Profile over suspended state failed"))',
            'when(form~>pinOrderBtn~>value error("Suspended state overrode profile false"))',
            # Loading profiles while sources are restricted must be atomic:
            # keep a saved user file, repair old CDL sources, defer bad-file
            # dialogs until the user edits the field or starts extraction.
            'foreach(inputType list("SVDB" "CCI")',
            f'  databaseData=list(list("input.type" inputType) list("extract.tool" "QRC") list("extract.output_type" "dspf") list("extract.corner" "Typ") list("extract.tech_dir" "{launch / "tech/Typ"}") list("netlist.pin_order_type" "User Defined File") list("netlist.pin_order_file" "{pins}"))',
            '  pinMessages=nil',
            '  unless(rceProfileApply(form databaseData) error("Database-input profile apply failed"))',
            f'  unless(and(form~>pinOrderBtn~>value form~>pinOrderType~>items==list("User Defined File") form~>pinOrderFile~>value=="{pins}") error("Database-input profile choice lost"))',
            '  unless(rceValidatePinOrder(form) error("Valid database-input profile rejected"))',
            '  databaseData=reverse(cddr(reverse(databaseData)))',
            '  unless(rceProfileApply(form databaseData) error("Legacy database-input profile failed"))',
            '  unless(and(form~>pinOrderBtn~>value form~>pinOrderType~>value=="User Defined File") error("Legacy database-input pin source not repaired"))',
            '  when(pinMessages error("Profile restoration triggered premature validation"))',
            ')',
            'unless(rceProfileApply(form data) error("Paired-input profile restore failed"))',
            'foreach(tool list("QRC" "StarRC" "CalXRC")',
            '  form~>extTool~>value=tool',
            '  rceRestorePinOrder(form t "User Defined File" "missing.cdl")',
            '  pinMessages=nil',
            '  when(rceValidatePinOrder(form) error("Missing pin file accepted for %s" tool))',
            '  unless(pinMessages error("Missing dialog for %s" tool))',
            f'  rceRestorePinOrder(form t "User Defined File" "{pins}")',
            '  unless(eq(form~>pinOrderBtn~>enabled t) error("Pin-order button not enabled for %s" tool))',
            '  unless(rceValidatePinOrder(form) error("Valid pin file rejected for %s" tool))',
            ')',
            f'deleteFile("{pins}")',
            'when(rceValidatePinOrder(form) error("Removed pin file accepted"))',
            "putd('GUI_messageApp savedMessage)",
            "hiFormClose(form) hiDeleteForm(form)",
        ),
        env_updates={
            "CAD_HOME": str(install), "CDS_LIB": "cds.lib",
            "RCE_DB_DIR": str(tmp_path), "RCE_DEF_TOOL": "QRC",
            "RCE_OUTPUT_CHOICES": "dspf,sp,spef", "RCE_CORNER": "Typ",
            "QUANTUS_TECH_DIR": "probe,tech/Typ",
            "STARRC_TECH_DIR": "probe,tech/Typ",
            "CALXRC_TECH_DIR": "probe,tech/Typ",
        },
    )
    assert not re.search(r"\*WARNING\*.*field 'pinOrder", output), output
