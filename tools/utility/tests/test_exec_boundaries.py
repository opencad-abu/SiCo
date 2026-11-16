"""Managed execution stays independent from session composition and history."""

from pathlib import Path

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import check_rule, load_manifest


ROOT = Path(__file__).resolve().parents[3]
RULES = {rule["id"]: rule for rule in load_manifest()["rules"]
         if rule["id"] in ("codex-managed-exec-no-application-owner", "terminal-command-channel-no-launcher",
                           "managed-bash-composition-no-product-route", "managed-bash-relay-no-host-policy")}


@pytest.mark.parametrize("rule", RULES.values(), ids=RULES)
def test_managed_exec_sources_obey_boundary(rule):
    assert not check_rule(rule, root=ROOT)


@pytest.mark.parametrize("module", ["sico.storage", "sico_ui", "sico.providers"])
@pytest.mark.parametrize("rule", RULES.values(), ids=RULES)
def test_alternate_owner_or_process_launcher_cannot_enter_adapter(module, rule):
    assert violations(f"def execute():\n    import {module}", rule, module="sico.codex.exec_process")
