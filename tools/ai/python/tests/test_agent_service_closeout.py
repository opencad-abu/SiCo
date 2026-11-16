"""AS-07C keeps historical local composition out of production entry paths."""

import importlib.util

import pytest
from agent_boundary_guard import ROOT, contract, production_violations
from utility.boundary_rules import violations

from sico.service.frontend_startup import FrontendStartup
from sico.service.remote_queries import RemoteSource
from sico.service.service_channel import ServiceChannel
from sico.service.worker import WorkerApi


@pytest.mark.parametrize("module", [
    "sico.service.startup", "sico.service.sessions",
    "sico.service.frontend_port", "sico.service.frontend_queries",
    "sico.transport.bridge",
])
def test_retired_runtime_module_has_no_import_or_native_input(module):
    import json

    assert importlib.util.find_spec(module) is None
    inventory = json.loads((ROOT / "deploy/python_native_build.json").read_text())
    assert module not in {row["name"] for row in inventory["modules"]}


@pytest.mark.parametrize("rule", ["agent-service-no-test-harness", "agent-ui-no-test-harness"])
def test_runtime_cannot_import_test_owned_composition(rule):
    assert not production_violations(rule)
    for module in ("agent_startup_harness", "agent_frontend_harness", "agent_sessions_harness"):
        assert violations("from " + module + " import Owner\nOwner()", contract(rule))


def test_live_client_has_no_local_backend_construction_path():
    from sico.service import local_startup, service_management

    assert not hasattr(local_startup, "LocalStartup")
    assert not hasattr(WorkerApi, "frontend")
    assert not hasattr(ServiceChannel, "request")
    assert not hasattr(service_management, "query_service")
    assert RemoteSource.__bases__ == (object,)
    assert not {"_journal", "_provider", "_sessions"} & set(dir(FrontendStartup))
