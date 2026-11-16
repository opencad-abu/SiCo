"""Captured snapshots remain historical evidence, independent of runtime availability."""

from copy import deepcopy
import json

import pytest

from sico.core.contracts import BoundContext
from sico.storage.session_snapshot import capture, fingerprint, read, save, validate
from sico.transport.framing import ProtocolError


def snapshot(protocol="responses"):
    config = dict(provider=protocol, base_url="https://example.test/v1",
                  model="fixture", api_key_env="MODEL_CREDENTIAL")
    if protocol == "responses":
        config["external_resources"] = "/unavailable/resources.json"
    runtime = dict(virtuoso="/unavailable/virtuoso", context="/unavailable/runtime.cxt",
                   context_sha256="0" * 64, version="fixture",
                   libraries={"Work": "/unavailable/library"})
    lsf = dict(cluster="test", owner="fixture", queue="batch",
               shared_root="/unavailable/shared", node_command=["/unavailable/python"],
               **{name: "/unavailable/" + name for name in
                  ("bsub", "bjobs", "bkill", "bhist", "lsid")})
    return capture("session", BoundContext("instance", "generation", "target", {}),
                   config, {"PATH": "/bin", "MODEL_CREDENTIAL": "secret",
                            "SICO_VIRTUOSO_PATH": "", "DISPLAY": ":99"},
                   {"bridge_id": "bridge", "router_id": "router", "token": "secret"},
                   "project", background=dict(runtime=runtime, lsf=lsf))


def resign(row):
    row["revision"] = fingerprint({k: v for k, v in row.items() if k != "revision"})
    return row


@pytest.mark.parametrize("protocol", ["anthropic", "openai", "responses"])
@pytest.mark.parametrize("background", ["lsf", "local", "none", "historical"])
def test_snapshot_roundtrip_keeps_original_bytes_and_does_not_normalize(tmp_path, protocol, background):
    row = snapshot(protocol)
    if background == "local":
        row["background"]["lsf"] = None
    elif background == "none":
        row["background"] = None
    elif background == "historical":
        row.pop("background")
        row["environment"] = {"CAD_PYTHON": "/historical/python"}
    resign(row)
    original = deepcopy(row)
    assert validate(row, "session") is row
    assert row == original
    assert "backend" not in row["provider_config"]
    assert "secret" not in json.dumps(row)
    assert "token" not in row["bridge_identity"]
    directory = tmp_path / "agent/sessions/session"
    directory.mkdir(parents=True, mode=0o700)
    for parent in (directory.parent, directory.parent.parent):
        parent.chmod(0o700)
    save(directory, row)
    path = directory / "session.snapshot.json"
    before = path.read_bytes()
    assert read(directory) == original
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", [
    "provider_secret", "provider_timeout", "codex_format", "runtime_unknown",
    "runtime_path", "lsf_budget", "lsf_command", "environment_secret",
    "bridge_token", "revision",
])
def test_invalid_snapshot_is_rejected_without_rewriting(tmp_path, damage):
    row = snapshot()
    if damage == "provider_secret":
        row["provider_config"]["api_key"] = "secret"
    elif damage == "provider_timeout":
        row["provider_config"]["timeout"] = False
    elif damage == "codex_format":
        row["provider_config"]["tool_format"] = "other"
    elif damage == "runtime_unknown":
        row["background"]["runtime"]["unknown"] = True
    elif damage == "runtime_path":
        row["background"]["runtime"]["context"] = None
    elif damage == "lsf_budget":
        row["background"]["lsf"]["slots"] = True
    elif damage == "lsf_command":
        row["background"]["lsf"]["node_command"] = "shell string"
    elif damage == "environment_secret":
        row["environment"]["MODEL_CREDENTIAL"] = "secret"
    elif damage == "bridge_token":
        row["bridge_identity"]["token"] = "secret"
    resign(row)
    if damage == "revision":
        row["revision"] = "0" * 64
    original = deepcopy(row)
    with pytest.raises(ProtocolError, match="Invalid captured session configuration"):
        validate(row, "session")
    assert row == original


def test_snapshot_preserves_unset_provenance_for_later_shell_restore(tmp_path):
    from cadenv import preserve_virtuoso_environment
    from sico.service.terminal_environment import shell_environment

    source = {"PYTHONPATH": "", "UNREGISTERED_SECRET": "never-persist"}
    preserve_virtuoso_environment(source)
    source.update(LD_LIBRARY_PATH="/runtime/lib", PYTHONPATH="/runtime/python")
    row = capture("session", BoundContext("instance", "generation", "target", {}),
                  None, source, {})
    validate(row, "session")
    assert row["environment"]["SICO_VIRTUOSO_LD_LIBRARY_PATH_SET"] == "0"
    assert shell_environment(row["environment"]) == {"PYTHONPATH": ""}
    assert "never-persist" not in json.dumps(row)
    directory = tmp_path / "agent/sessions/session"
    directory.mkdir(parents=True, mode=0o700)
    for parent in (directory.parent, directory.parent.parent):
        parent.chmod(0o700)
    save(directory, row)
    restored = read(directory)
    assert restored == row
    assert shell_environment(restored["environment"]) == {"PYTHONPATH": ""}


def test_snapshot_keeps_structured_data_roots():
    source = {
        "SICO_PDK_DATA": "/shared/pdk/index.json",
        "SICO_CIRCUIT_TEMPLATES_DIR": "/shared/templates",
        "UNREGISTERED_SECRET": "never-persist",
    }
    row = capture("session", BoundContext("instance", "generation", "target", {}),
                  None, source, {})
    validate(row, "session")
    assert row["environment"]["SICO_PDK_DATA"] == "/shared/pdk/index.json"
    assert row["environment"]["SICO_CIRCUIT_TEMPLATES_DIR"] == "/shared/templates"
    assert "UNREGISTERED_SECRET" not in row["environment"]


@pytest.mark.parametrize("credential", ["SICO_VIRTUOSO_PATH", "SICO_VIRTUOSO_PATH_SET"])
def test_snapshot_does_not_reinterpret_credentials_as_provenance(credential):
    config = dict(provider="openai", base_url="https://example.test/v1",
                  model="fixture", api_key_env=credential)
    source = {"SICO_VIRTUOSO_PATH": "synthetic-secret", "SICO_VIRTUOSO_PATH_SET": "1"}
    row = capture("session", BoundContext("instance", "generation", "target", {}),
                  config, source, {})
    validate(row, "session")
    assert row["environment"] == {}
