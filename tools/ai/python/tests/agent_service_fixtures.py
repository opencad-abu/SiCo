"""Deterministic process and socket fixtures for project service ownership tests."""

import os
import socket
from contextlib import contextmanager

from sico.service.project_service import ProjectBusy, ProjectServiceLease


@contextmanager
def private_socket(directory):
    directory.mkdir(mode=0o700)
    path = directory / "s"
    sock = socket.socket(socket.AF_UNIX)
    try:
        sock.bind(str(path))
        path.chmod(0o600)
        sock.listen(1)
        yield path
    finally:
        sock.close()


def compete(project, barrier, release, connection):
    lease = None
    try:
        barrier.wait(timeout=15)
        try:
            lease = ProjectServiceLease.acquire(project)
        except ProjectBusy as exc:
            connection.send(("busy", exc.discovery.state))
        else:
            connection.send(("owner", lease.project_id, lease.service_id))
            if not release.wait(15):
                raise TimeoutError("Parent did not release owner")
    except BaseException as exc:
        connection.send(("error", repr(exc)))
        raise
    finally:
        if lease is not None:
            lease.close()
        connection.close()


def hold(project, connection):
    try:
        with ProjectServiceLease.acquire(project) as lease:
            connection.send((lease.project_id, lease.service_id, os.getpid()))
            connection.recv()
    finally:
        connection.close()


def receive(connection):
    assert connection.poll(15), "Child did not respond"
    return connection.recv()


def reap(process):
    process.join(10)
    if process.is_alive():
        process.kill()
        process.join(5)
        raise AssertionError("Child did not exit")
