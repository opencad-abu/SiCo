"""Old-kernel service termination by its unreaped direct-child owner.

Compatibility for hosts without pidfds; retire when those hosts leave the target
matrix. Never signal an unrelated process based on a persisted PID snapshot.
"""

import ctypes
import json
import os
import select
import signal
import socket
import struct
import threading
import time
import traceback

from sicoprocess import enable_subreaper, reap_descendants, require_pidfds


def address(record):
    return "\0sico-custody:" + str(os.getuid()) + ":" + record["service_id"]


def peer(sock):
    return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def send(sock, row):
    sock.sendall(json.dumps(row, separators=(",", ":")).encode())


def receive(sock):
    data = sock.recv(16385)
    if not data or len(data) > 16384:
        raise ValueError("Invalid service custody frame")
    return json.loads(data)


def publish(channel, descriptor):
    if channel is None:
        return
    channel.settimeout(5)
    send(channel, descriptor.record())
    if receive(channel) != {"ready": True}:
        raise ValueError("Service custody was not established")
    channel.close()


def stopped(pid):
    # WNOWAIT leaves this direct child's PID reserved until all signals finish.
    return os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)


def terminate(pid, grace):
    if stopped(pid):
        return False
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while not stopped(pid) and time.monotonic() < deadline:
        time.sleep(.02)
    escalated = not stopped(pid)
    if escalated:
        os.kill(pid, signal.SIGKILL)
    return escalated


def own(pid, channel):
    listener, record, reply = None, None, None
    requested = []
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(number, lambda *_: requested.append(True))
    try:
        while not stopped(pid):
            if requested:
                terminate(pid, .5)
                break
            handles = [listener] if listener else [channel]
            if not select.select(handles, [], [], .05)[0]:
                continue
            if listener is None:
                candidate = receive(channel)
                if candidate.get("pid") != pid:
                    raise ValueError("Service custody child identity mismatch")
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET)
                listener.bind(address(candidate))
                listener.listen(4)
                record = candidate
                send(channel, {"ready": True})
                channel.close()
                continue
            client, _ = listener.accept()
            client.settimeout(.5)
            try:
                _, uid, _ = peer(client)
                request = receive(client)
                if uid != os.getuid() or request.get("service") != record:
                    send(client, {"error": "Service custody identity mismatch"})
                    continue
                grace = request.get("grace")
                if type(grace) not in (int, float) or not 0 <= grace <= 5:
                    raise ValueError("Invalid service custody deadline")
                escalated = terminate(pid, grace)
                reply = (client, escalated)
                break
            except (OSError, ValueError):
                pass
            finally:
                if reply is None:
                    client.close()
        state = stopped(pid)
        while state is None:
            time.sleep(.02)
            state = stopped(pid)
        status = state.si_status if state.si_code == os.CLD_EXITED else 128 + state.si_status
        reap_descendants(.5)
        if reply:
            send(reply[0], {"stopped": True, "escalated": reply[1]})
        return status
    finally:
        if reply:
            reply[0].close()
        channel.close()
        if listener:
            listener.close()
        reap_descendants(.5)


def run(function, project, *, bootstrap_fd, idle_seconds):
    try:
        require_pidfds()
    except ValueError:
        pass
    else:
        return function(project, bootstrap_fd=bootstrap_fd, idle_seconds=idle_seconds)
    if threading.active_count() != 1:
        raise RuntimeError("Old-kernel service custody must start before service threads")
    enable_subreaper()
    parent, child = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    owner = os.getpid()
    pid = os.fork()
    if pid == 0:
        parent.close()
        try:
            # The custody process owns this service's lifetime. Kernel delivery
            # also works when that owner is killed before it can run cleanup.
            libc = ctypes.CDLL(None, use_errno=True)
            if libc.prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
                raise OSError(ctypes.get_errno(), "Cannot bind service lifetime")
            if os.getppid() != owner:
                os._exit(1)
            status = function(project, bootstrap_fd=bootstrap_fd,
                              idle_seconds=idle_seconds, custody=child)
        except BaseException:
            traceback.print_exc()
            status = 1
        os._exit(status)
    child.close()
    if bootstrap_fd is not None:
        os.close(bootstrap_fd)
    return own(pid, parent)


def request_stop(descriptor, *, grace, deadline):
    # The server is a parent holding a waitable child, not an arbitrary PID sender.
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as client:
        client.settimeout(max(.01, deadline - time.monotonic()))
        client.connect(address(descriptor.record()))
        parent, uid, _ = peer(client)
        from pathlib import Path
        fields = Path(f"/proc/{descriptor.pid}/stat").read_text().rsplit(")", 1)[1].split()
        if uid != os.getuid() or int(fields[1]) != parent or fields[19] != descriptor.process_start:
            raise ValueError("Service custody ownership mismatch; no signal sent")
        send(client, {"service": descriptor.record(), "grace": min(grace, 5)})
        result = receive(client)
        if result.get("stopped") is not True:
            raise RuntimeError("Service custody cleanup unconfirmed")
        return result["escalated"]
