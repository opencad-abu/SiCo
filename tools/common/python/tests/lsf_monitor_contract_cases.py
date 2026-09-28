from __future__ import annotations

import os
from pathlib import Path

from skill_test_support import read_skill_source

CAD_ROOT = Path(__file__).resolve().parents[3]
RUN_PROBE = os.environ.get("RCE_RUN_SKILL_PROBE") == "1"


def test_lsf_monitor_selector_is_owned_validated_and_shared() -> None:
    source = (CAD_ROOT / "common/skill/SICO_lsfMonitor.il").read_text(
        encoding="utf-8"
    )
    helper = read_skill_source(CAD_ROOT / "common/skill/SICO_lsf.il")
    base = read_skill_source(CAD_ROOT / "common/skill++/BASEGUI.ils")

    assert '"20260924.sico.monitor.environment.v3"' in source
    assert 'SICO_envValue("SICO_LSF_BKILL")' in source
    assert '" --bkill " SICO_shellQuote(bkill)' in source
    assert 'SICO_iconPath(SICO_installationRoot() "actions" "monitor.png")' in source
    assert 'strcat(toolsRoot "/share/logo.png")' not in source
    assert "cadLsfMonitorIconCachePath==path" in source
    assert '" --output "' in source
    assert "procedure(SICO_lsfMonitorStartSelector(form)" in source
    assert "procedure(SICO_lsfMonitorCancelForm(form)" in source
    assert "SICO_lsfProtocolRecords(" in source
    assert 'list("QUEUE" "HOST") list("applied")' in source
    assert "queueCount!=1" in source
    assert "hostCount>1" in source
    assert "cadLsfMonitorRequestToken" in source
    assert "state['cid]==cid" in source
    assert 'form~>runType~>value=="LSF Farm"' in source
    assert "SICO_lsfSetPreferredValues(form cadr(result) caddr(result))" in source
    assert "SICO_lsfStartQueueDiscovery(form)" in source
    assert "regExitBefore('SICO_lsfMonitorStopAll)" in source
    assert "SICO_lsfMonitorCancelForm(form)" in helper
    assert "procedure(SICO_lsfUpdateQueueField(form items value)" in helper
    assert "cadLsfQueueUpdateActive" in helper
    assert "unwindProtect(" in helper
    assert "!state['cancelled]" in helper
    assert "procedure(cadLsfMonitorSelectorCB(form)" in base
    assert "SICO_lsfMonitorStartSelector(form)" in base
    assert '?callback   "cadLsfMonitorSelectorCB(hiGetCurrentForm())"' in base
    assert '?callback   "(when(' not in base
    assert "?toolTip" in base
    assert "?buttonIcon" in base

    for flow in ("drc", "lvs", "rce", "lef"):
        loader = (CAD_ROOT / f"{flow}/skill++/{flow.upper()}.ils").read_text(
            encoding="utf-8"
        )
        assert 'SICO_lsfMonitorRevision()=="20260924.sico.monitor.environment.v3"' in loader
        assert (
            'cadBaseHardwareRevision()=="20260820.lsf.monitor.v7"'
            in loader
        )
        protocol_at = loader.index('/skill/SICO_guiProtocol.il")')
        lsf_at = loader.index('/skill/SICO_lsf.il")')
        monitor_at = loader.index('/skill/SICO_lsfMonitor.il")')
        assert protocol_at < lsf_at < monitor_at
