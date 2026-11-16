"""Local endpoint policy and partial startup cleanup belong to the service process."""

import socket
import time
from io import StringIO
from pathlib import Path

import pytest

from sico.service import service_runtime
from sico.service.project_service import ProjectServiceLease
from sico.service.service_entry import run_service
from sico.service.service_runtime import listening_socket, validate_endpoint
from sico.storage.project_files import RegistryError
from sico.storage.project_locking import validate_local_runtime
from sico.transport.framing import Connection


@pytest.mark.parametrize("filesystem,options", [
    ("nfs4", "rw,local_lock=none"), ("cifs", "rw"), ("ext4", "ro"),
])
def test_socket_runtime_rejects_network_and_readonly_mounts(
    tmp_path, monkeypatch, filesystem, options,
):
    text = f"1 0 0:1 / / rw - {filesystem} source {options}\n"
    monkeypatch.setattr(Path, "open", lambda *_a, **_kw: StringIO(text))
    with pytest.raises(RegistryError, match="local filesystem"):
        validate_local_runtime(tmp_path)


def test_runtime_ignores_project_temp_environment(tmp_path, monkeypatch):
    for name in ("TMPDIR", "TMP", "TEMP", "XDG_RUNTIME_DIR"):
        monkeypatch.setenv(name, str(tmp_path))
    with listening_socket() as (_listener, endpoint):
        directory = Path(endpoint).parent
        assert directory.parent == Path("/tmp")
        validate_endpoint(endpoint)
    assert not directory.exists()


@pytest.mark.parametrize("boundary", ["bind", "listen", "publication", "published"])
def test_partial_service_construction_cleans_only_own_runtime(tmp_path, monkeypatch, boundary):
    directories = []
    original = service_runtime.tempfile.mkdtemp
    real_socket = socket.socket
    real_publish = ProjectServiceLease.publish

    def directory(**kwargs):
        result = original(**kwargs)
        directories.append(Path(result))
        return result

    class FailingSocket(real_socket):
        def bind(self, address):
            if boundary == "bind":
                raise OSError("injected bind failure")
            return super().bind(address)

        def listen(self, count):
            if boundary == "listen":
                raise OSError("injected listen failure")
            return super().listen(count)

    def publish(self, endpoint):
        if boundary == "published":
            real_publish(self, endpoint)
        raise OSError("injected publication failure")

    monkeypatch.setattr(service_runtime.tempfile, "mkdtemp", directory)
    parent, child = socket.socketpair()
    with listening_socket() as (_other, other_endpoint):
        other_directory = Path(other_endpoint).parent
        monkeypatch.setattr(service_runtime.socket, "socket", FailingSocket)
        if boundary in {"publication", "published"}:
            monkeypatch.setattr(ProjectServiceLease, "publish", publish)
        try:
            assert run_service(tmp_path, bootstrap_fd=child.detach()) == 1
            reply = Connection(parent).receive(deadline=time.monotonic() + 2)
            assert reply["kind"] == "failed" and reply["code"] == "OSError"
            assert directories[0] == other_directory and other_directory.exists()
            assert len(directories) == 2 and not directories[1].exists()
            with ProjectServiceLease.acquire(tmp_path):
                pass
        finally:
            parent.close()
            child.close()


def test_endpoint_validation_rejects_symlink_and_public_socket(tmp_path):
    with listening_socket() as (_listener, endpoint):
        path = Path(endpoint)
        path.chmod(0o666)
        with pytest.raises(RegistryError, match="private owned"):
            validate_endpoint(path)
        path.chmod(0o600)
        alias = tmp_path / "alias"
        alias.symlink_to(path.parent, target_is_directory=True)
        with pytest.raises(RegistryError, match="ancestor"):
            validate_endpoint(alias / path.name)
