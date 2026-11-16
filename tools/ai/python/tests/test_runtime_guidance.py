"""Historical service instruction imports are exact aliases of their owners."""

from sico.codex import audit_instructions
from sico.core import workbench_instructions
from sico.service import audit, audit_instructions as legacy_audit, workbench


def test_service_guidance_exports_have_one_authoritative_value():
    assert workbench.INSTRUCTIONS is workbench_instructions.INSTRUCTIONS
    assert legacy_audit.INSTRUCTIONS is audit_instructions.INSTRUCTIONS
    assert audit.INSTRUCTIONS is audit_instructions.INSTRUCTIONS
