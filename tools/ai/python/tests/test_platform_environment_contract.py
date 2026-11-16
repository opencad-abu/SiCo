"""Bridge snapshots keep finite provenance and service compatibility semantics."""

import pytest

from sico.service import background_environment as service
from sico.transport import platform_environment as contract
from sico.transport.framing import ProtocolError


@pytest.mark.parametrize("captured", ["", "/eda/bin"])
def test_capture_prefers_registered_provenance_and_excludes_unrelated_values(captured):
    source = {
        "PATH": "/host/bin", "SICO_VIRTUOSO_PATH": captured,
        "CAD_VIRTUOSO_PATH": "/retired/bin", "HOME": "/home/fixture",
        "SICO_VIRTUOSO_LD_LIBRARY_PATH": "/eda/lib",
        "SICO_VIRTUOSO_PYTHONPATH": "/eda/python",
        "LD_LIBRARY_PATH": "/host/lib", "PYTHONPATH": "/host/python",
        "SICO_API_KEY": "secret", "DISPLAY": ":99", "UNREGISTERED": "excluded",
        "SICO_VIRTUOSO_SECRET": "excluded",
    }
    original = dict(source)
    assert contract.capture_environment(source) == {
        "PATH": captured, "HOME": "/home/fixture",
        "LD_LIBRARY_PATH": "/eda/lib", "PYTHONPATH": "/eda/python",
    }
    assert source == original


@pytest.mark.parametrize("credential", ["PATH", "SICO_VIRTUOSO_PATH", "CAD_VIRTUOSO_PATH",
                                       "SICO_VIRTUOSO_PATH_SET", "CAD_VIRTUOSO_PATH_SET"])
def test_credential_is_excluded_under_all_registered_spellings(credential):
    assert contract.capture_environment(
        {"PATH": "secret", "SICO_VIRTUOSO_PATH": "secret",
         "CAD_VIRTUOSO_PATH": "secret", "HOME": "/home/fixture"},
        credential=credential,
    ) == {"HOME": "/home/fixture"}


def test_retired_only_provenance_requires_migration():
    with pytest.raises(ValueError, match="removed"):
        contract.capture_environment({"CAD_VIRTUOSO_PATH": "/old/bin"})


@pytest.mark.parametrize("snapshot", [
    None, [], {"DISPLAY": ":99"}, {"PATH": None}, {"PATH": 1},
    {"PATH": "bad\0value"}, {"PATH": "x" * 8193},
])
def test_invalid_snapshots_keep_the_protocol_error(snapshot):
    with pytest.raises(ProtocolError, match="Invalid captured EDA environment"):
        contract.validate_environment(snapshot)


def test_validated_snapshot_is_detached_at_the_size_limit():
    source = {"PATH": "x" * 8192}
    copied = contract.validate_environment(source)
    assert copied == source and copied is not source
    copied["PATH"] = "changed"
    assert source["PATH"] == "x" * 8192


def test_service_exports_share_the_contract_objects():
    for name in ("EDA_NAMES", "CAPTURED_NAMES", "SNAPSHOT_NAMES",
                 "capture_environment", "validate_environment"):
        assert getattr(service, name) is getattr(contract, name)


def test_unset_capture_does_not_fall_back_to_runtime_values():
    from cadenv import preserve_virtuoso_environment

    source = {"MODULEPATH": "", "HOME": "/home/fixture"}
    preserve_virtuoso_environment(source)
    source.update(PATH="/runtime/bin", LD_LIBRARY_PATH="/runtime/lib")
    assert contract.capture_environment(source) == {"MODULEPATH": "", "HOME": "/home/fixture"}


def test_worker_materializes_captured_values_without_recapturing(tmp_path):
    snapshot = {"PATH": "/eda/bin", "LD_LIBRARY_PATH": "/eda/lib",
                "PYTHONPATH": "/eda/python", "CDS_AUTO_64BIT": ""}
    original = dict(snapshot)
    worker = service.worker_environment(tmp_path, snapshot, captured=True)
    assert snapshot == original
    assert worker["LD_LIBRARY_PATH"] == "/eda/lib"
    assert worker["PYTHONPATH"] == "/eda/python"
    assert worker["PATH"] == "/eda/bin"
    assert worker["CDS_AUTO_64BIT"] == "ALL"
    assert worker["PWD"] == str(tmp_path)
    assert worker["PYTHONNOUSERSITE"] == worker["PYTHONDONTWRITEBYTECODE"] == "1"
    for name in ("TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        assert worker[name] == str(tmp_path / "scratch")
