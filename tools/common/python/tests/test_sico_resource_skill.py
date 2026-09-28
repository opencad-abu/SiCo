"""Real Virtuoso icon loading uses the same product paths as Python."""

import json
from pathlib import Path

from skill_probe_support import run_virtuoso_source


ROOT = Path(__file__).resolve().parents[4]


def test_virtuoso_loads_flow_monitor_and_copilot_icons(tmp_path):
    inputs = [
        "tools/common/skill/SICO_environment.il",
        "tools/common/skill/SICO_installation.il",
        "tools/utility/skill/UI_windowIcon.il",
        "tools/common/skill/SICO_lsfMonitor.il",
        "tools/sico/skill/SICO_rmb.il",
    ]
    loads = "\n".join(f"load({json.dumps(str(ROOT / path))})" for path in inputs)
    forms = f'''{loads}
{loads}
sicoRoot={json.dumps(str(ROOT / 'tools/sico'))}
unless(SICO_windowIcon() error("flow icon missing"))
unless(SICO_lsfMonitorButtonIcon() error("monitor icon missing"))
unless(sicoAskIcon() error("copilot menu icon missing"))
unless(SICO_windowIconPath()==SICO_iconPath(SICO_installationRoot() "brand" "logo.png")
       error("different resource owners"))
printf("SICO_RESOURCE_ICONS_OK\\n")
exit()
'''
    output = run_virtuoso_source(forms, tmp_path,
        env_updates={"SICO_HOME": str(ROOT), "CAD_HOME": str(ROOT)})
    assert "SICO_RESOURCE_ICONS_OK" in output
