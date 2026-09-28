"""Terminate one captured local service generation without depending on its listener."""

import os
import select
import signal
import time
from dataclasses import dataclass
from pathlib import Path

from ..storage.project_files import read_record
from .background_watchdog import process_identity
from sicoprocess import require_pidfds
from .service_discovery import ServiceDescriptor, discover_project, local_host
from .service_runtime import validate_endpoint
from .service_waiter import ServiceWaiter

GRACE_SECONDS = 5.0
WAIT_SECONDS = 10.0


@dataclass(frozen=True)
class ForceStopResult:
    service_id: str
    escalated: bool = False


def _check(project, descriptor):
    found = discover_project(project)
    if found.state in {"absent", "inactive"}:
        return False
    if found.state != "local_candidate" or found.service != descriptor:
        raise ValueError("项目服务身份已变化，请重新打开窗口后操作；未终止其他服务。")
    return True


def _wait(fd, deadline):
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    return bool(poller.poll(max(0, int((deadline - time.monotonic()) * 1000))))


def force_stop(project, descriptor, *, deadline):
    """SIGTERM then bounded SIGKILL via a pinned pidfd; never unlink ownership/history."""
    if not Path(project).is_absolute() or type(descriptor) is not ServiceDescriptor:
        raise ValueError("强制停止需要明确的工程目录和原服务身份。")
    host = local_host()
    if (descriptor.host.host_id != host.host_id or descriptor.host.boot_id != host.boot_id
            or descriptor.pid == os.getpid()):
        raise ValueError("只能强制停止本机独立运行的原 Agent Service。")
    if not _check(project, descriptor):
        return ForceStopResult(descriptor.service_id)
    try:
        require_pidfds()
    except ValueError:
        from .service_custody import request_stop
        endpoint = Path(descriptor.endpoint)
        validate_endpoint(endpoint)
        if read_record(endpoint.parent / "owner.json") != descriptor.record():
            raise ValueError("原服务归属记录不匹配，未发送终止信号。")
        if not _check(project, descriptor):
            return ForceStopResult(descriptor.service_id)
        escalated = request_stop(descriptor, grace=min(GRACE_SECONDS,
                                 max(0, deadline - time.monotonic() - 1)), deadline=deadline)
        if discover_project(project).state not in {"absent", "inactive"}:
            raise RuntimeError("原服务清理后工程锁尚未释放；未删除会话记录。")
        return ForceStopResult(descriptor.service_id, escalated)
    try:
        fd = os.pidfd_open(descriptor.pid)
    except ProcessLookupError:
        if not _check(project, descriptor):
            return ForceStopResult(descriptor.service_id)
        raise
    try:
        # Revalidate after pinning: a reused PID must never become a new target.
        identity = process_identity(descriptor.pid)
        if (identity["start"] != descriptor.process_start
                or Path(f"/proc/{descriptor.pid}").stat().st_uid != os.getuid()):
            raise ValueError("原服务进程身份不匹配，未发送终止信号。")
        endpoint = Path(descriptor.endpoint)
        validate_endpoint(endpoint)
        if read_record(endpoint.parent / "owner.json") != descriptor.record():
            raise ValueError("原服务归属记录不匹配，未发送终止信号。")
        if not _check(project, descriptor):
            return ForceStopResult(descriptor.service_id)
        if time.monotonic() >= deadline:
            raise TimeoutError("核对服务身份超时，未发送终止信号。")
        escalated = False
        try:
            signal.pidfd_send_signal(fd, signal.SIGTERM)
            if not _wait(fd, min(deadline, time.monotonic() + GRACE_SECONDS)):
                escalated = True
                signal.pidfd_send_signal(fd, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if not _wait(fd, deadline):
            raise TimeoutError("原服务尚未退出；会话记录已保留，可再次查询或强制停止。")
        # The PID is dead. Check lease release separately; a replacement is never signalled.
        found = discover_project(project)
        if found.service == descriptor or found.state in {"starting", "unverified"}:
            raise RuntimeError("原服务已退出，但工程锁尚未释放；未删除锁或会话记录。")
        return ForceStopResult(descriptor.service_id, escalated)
    finally:
        os.close(fd)


class ForceServiceStop(ServiceWaiter):
    def __init__(self, project, descriptor):
        # Once explicitly requested, closing a UI only discards its receipt;
        # the bounded termination of this captured generation still completes.
        super().__init__(lambda deadline: force_stop(project, descriptor, deadline=deadline),
                         timeout=WAIT_SECONDS)
