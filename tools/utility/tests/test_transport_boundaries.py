"""EQ-08 transport payload contracts stay below the service layer."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from utility.boundary_rules import violations
from utility.check_private_import_boundaries import load_manifest


ROOT = Path(__file__).resolve().parents[3]
for source_root in (
    ROOT / "tools/ai/python",
    ROOT / "tools/sico/python",
    ROOT / "tools/common/python",
):
    if str(source_root) not in sys.path:
        sys.path.insert(0, str(source_root))
ALL_RULES = {
    rule["id"]: rule
    for rule in load_manifest()["rules"]
}


def test_transport_host_payload_modules_keep_one_way_boundaries():
    for rule_id, module in (
        ("transport-host-payload-contract-runtime-free", "sico.transport.host_payload_contract"),
        ("transport-host-payloads-one-way-boundary", "sico.transport.host_payloads"),
    ):
        rule = ALL_RULES[rule_id]
        path = ROOT / rule["paths"][0]
        assert not violations(
            path.read_text(encoding="utf-8"),
            rule,
            path=path.relative_to(ROOT).as_posix(),
            module=module,
        )


def test_transport_payload_contract_imports_without_service_runtime(tmp_path):
    source_roots = [
        ROOT / "tools/ai/python",
        ROOT / "tools/sico/python",
        ROOT / "tools/common/python",
    ]
    script = "import sys\nsys.path[:0] = " + repr([str(path) for path in source_roots]) + "\n" + r'''
import importlib.abc
import sys

BLOCKED = ("sico.service", "sico.providers", "sico.storage", "sico_ui", "PyQt5", "PySide")

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if any(fullname == prefix or fullname.startswith(prefix + ".")
               for prefix in BLOCKED):
            raise ImportError("blocked runtime import: " + fullname)
        return None

sys.meta_path.insert(0, Block())
from sico.transport.host_payload_contract import capture_payload
from sico.transport.host_payloads import HostPayloads

context = {"contract": "virtuoso_context.v1", "instance_id": "i",
           "generation": "g", "target_id": "t", "snapshot": {}}
payload = {"message": {"kind": "submit", "id": "t", "text": "Ask",
                         "context": context},
           "provider_config": None, "environment": {"PATH": "value"}}
assert capture_payload(payload) == payload
store = HostPayloads()
reference = store.stage(object(), payload)
assert store.read(reference["payload_id"]) == payload
assert not any(name == "sico.service" or name.startswith("sico.service.")
               for name in sys.modules)
'''
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in source_roots)
    result = subprocess.run(
        [sys.executable, "-B", "-S", "-c", script],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_service_capture_payload_is_a_compatibility_export():
    from sico.core.host_contracts import digest
    from sico.service import host_contract as service
    from sico.transport import host_payload_contract as transport

    assert service.capture_payload is transport.capture_payload
    assert service.PAYLOAD_LIMIT == transport.PAYLOAD_LIMIT
    assert service.digest is digest


@pytest.mark.parametrize("rule_id,module", [
    ("transport-host-payload-contract-runtime-free", "sico.transport.host_payload_contract"),
    ("transport-host-payloads-one-way-boundary", "sico.transport.host_payloads"),
])
@pytest.mark.parametrize("source", [
    "from ..service.host_contract import capture_payload",
    "import sico.service.host_contract as contract",
])
def test_reverse_service_imports_are_rejected(rule_id, module, source):
    assert violations(source, ALL_RULES[rule_id], module=module)
