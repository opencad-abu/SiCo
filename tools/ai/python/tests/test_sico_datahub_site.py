"""Read-only pyaether/Custom Compiler site capability probes."""

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sico_datahub.endpoint import UnixEnvelopeServer
from sico_datahub.envelope import Envelope, Meta
from sico_datahub.errors import HubError
from sico_datahub.site import (
    default_probe_path,
    probe_capabilities,
    probe_site,
    safe_site_environment,
)

ROOT = Path(__file__).resolve().parents[4]
PROBE = default_probe_path()
WIRE = ROOT / "tools/common/python/sico_datahub_wire.py"
PYAETHER = Path(
    "/software/eda/empyrean/installs/aether_2025.06.hf1_linux-x86_64_rhel6_20250806/"
    "tools/platform/linux26-x86_64/python/3.9/bin/python3.9"
)
CUSTOM_COMPILER = Path(
    "/software/eda/synopsys/installs/customcompiler/V-2023.12-SP1/bin/python3.8"
)


def test_site_probe_is_python_38_syntax_and_stdlib_only():
    source = PROBE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(PROBE), feature_version=(3, 8))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add((node.module or "").split(".")[0])
    assert imports <= {
        "argparse", "base64", "importlib", "json", "os", "platform",
        "runpy", "socket", "sys", "types",
    }
    assert "sico" not in imports


@pytest.mark.parametrize("mode", ["import", "spec", "runpy", "exec"])
def test_local_python_probe_reports_all_load_modes(mode):
    envelope = probe_site(
        sys.executable,
        wire_path=WIRE,
        probe_path=PROBE,
        load_mode=mode,
        scope="local-test",
    )
    assert envelope.status == "ok"
    payload = envelope.payload
    assert payload["schema"] == "site.capability.v1"
    assert payload["scope"] == "local-test"
    assert payload["wire"]["hub"] == "sico.datahub.v1"
    assert payload["wire"]["load_mode"] == mode
    assert payload["capabilities"]["read_only"] is True
    assert all(payload["capabilities"]["wire_load_methods"][name]["ok"]
               for name in ("import", "spec", "runpy", "exec"))


@pytest.mark.parametrize(
    "executable, expected_prefix",
    [
        (PYAETHER, "3.9."),
        (CUSTOM_COMPILER, "3.8."),
    ],
)
def test_real_site_python_probe(executable, expected_prefix):
    if not executable.is_file():
        pytest.skip(f"site Python unavailable: {executable}")
    payload = probe_capabilities(executable, wire_path=WIRE, scope=executable.name)
    assert payload["runtime"]["version"].startswith(expected_prefix)
    assert payload["capabilities"]["json"] is True
    assert payload["capabilities"]["base64"] is True
    assert all(
        payload["capabilities"]["wire_load_methods"][mode]["ok"]
        for mode in ("import", "spec", "runpy", "exec")
    )
    assert payload["wire"]["exports"]


def test_site_probe_load_failure_returns_envelope(tmp_path):
    broken = tmp_path / "broken_wire.py"
    broken.write_text("raise RuntimeError('broken wire')\n", encoding="utf-8")
    envelope = probe_site(
        sys.executable,
        wire_path=broken,
        probe_path=PROBE,
        load_mode="import",
    )
    assert envelope.status == "error"
    assert envelope.error.code == "capability_not_available"
    assert "load_methods" in envelope.error.details


def test_safe_environment_removes_credentials_but_keeps_runtime_paths():
    values = safe_site_environment({
        "PATH": "/bin",
        "PYTHONPATH": "/site/python",
        "SICO_API_KEY": "secret",
        "OPENAI_API_KEY": "secret",
        "MY_TOKEN": "secret",
        "NORMAL_VALUE": "kept",
    })
    assert values == {
        "PATH": "/bin",
        "PYTHONPATH": "/site/python",
        "NORMAL_VALUE": "kept",
    }


def test_site_probe_rejects_missing_executable(tmp_path):
    with pytest.raises(HubError) as failure:
        probe_site(tmp_path / "missing-python", wire_path=WIRE, probe_path=PROBE)
    assert failure.value.code == "capability_not_available"


@pytest.mark.parametrize(
    "executable, isolate",
    [
        (Path(sys.executable), True),
        (PYAETHER, True),
        (CUSTOM_COMPILER, False),
    ],
)
def test_site_python_endpoint_client(tmp_path, executable, isolate):
    if not executable.is_file():
        pytest.skip(f"site Python unavailable: {executable}")

    def handler(request):
        return Envelope(
            contract=request.contract,
            version=request.version,
            action=request.action,
            id=request.id,
            payload={
                "schema": "site.capability.v1",
                "scope": request.payload["scope"],
                "read_only": True,
            },
            meta=Meta(source="python", target="python-site"),
        )

    with UnixEnvelopeServer(
        tmp_path / "endpoint.sock",
        "endpoint-token",
        "endpoint-session",
        {("sico.site.capability.v1", "probe"): handler},
    ) as server:
        script = tmp_path / "site-client.py"
        script.write_text(
            "import importlib.util\n"
            f"spec=importlib.util.spec_from_file_location('wire', {str(WIRE)!r})\n"
            "wire=importlib.util.module_from_spec(spec); spec.loader.exec_module(wire)\n"
            f"with wire.EndpointClient({str(server.path)!r}, "
            "'endpoint-token', 'endpoint-session') as client:\n"
            "    reply=client.exchange('sico.site.capability.v1', 1, 'probe', "
            "'site-1', {'scope': 'site-endpoint'})\n"
            "    print('SITE_ENDPOINT', reply['payload']['scope'], "
            "','.join(client.capabilities))\n",
            encoding="utf-8",
        )
        command = [str(executable)]
        if isolate:
            command.append("-I")
        command.append(str(script))
        result = subprocess.run(
            command,
            env=dict(os.environ, TMPDIR="/tmp"),
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "SITE_ENDPOINT site-endpoint site.python" in result.stdout
