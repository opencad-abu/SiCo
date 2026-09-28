"""Authenticated, project-local CDNS-IC bridge discovery; descriptors stay private."""

import os
import re
import time
from contextlib import ExitStack

from ..core.contracts import BoundContext
from ..storage.journal import open_private
from ..storage.roots import agent_root
from .bridge_client import BridgeClient
from .bridge_identity import discovery_path
from .bridge_tunnel import node_name
import socket
from .framing import strict_json

LIMIT = 32


def candidates(project):
    paths = []
    for path in (agent_root(project) / "bridges").glob("*.json"):
        try:
            paths.append((path.stat().st_mtime_ns, path))
        except OSError:
            continue
    return [path for _, path in sorted(paths, reverse=True)]


def descriptor_at(project, path):
    with os.fdopen(open_private(path, os.O_RDONLY), "rb") as stream:
        descriptor = strict_json(stream.read(8193))
    context = BoundContext(descriptor["instance_id"], descriptor["generation"], "bound", {})
    node = node_name(descriptor.get("node", socket.gethostname()))
    if path != discovery_path(project, context, node=node):
        raise ValueError("实例执行主机或身份已变化")
    return descriptor


def connect_descriptor(project, descriptor, *, deadline=None, cancelled=None):
    deadline = deadline or time.monotonic() + 8
    with ExitStack() as pending:
        bridge = pending.enter_context(BridgeClient(descriptor, deadline=deadline,
                                                   cancelled=cancelled))
        context = bridge.context(descriptor["instance_id"], timeout=0,
            deadline=deadline, cancelled=cancelled)
        if (context.generation != descriptor["generation"]
                or not os.path.samefile(project, context.snapshot.get("cwd", ""))):
            raise ValueError("实例身份或所属工程已变化")
        pending.pop_all()
        return bridge, context


def connect_candidate(project, candidate, *, cancelled=None):
    if not isinstance(candidate, str) or re.fullmatch(r"[0-9a-f]{64}", candidate) is None:
        raise ValueError("无效的平台实例，请刷新列表")
    path = agent_root(project) / "bridges" / (candidate + ".json")
    try:
        descriptor = descriptor_at(project, path)
        return connect_descriptor(project, descriptor, cancelled=cancelled)
    except (OSError, EOFError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        raise ValueError("CDNS-IC 实例已不可用，请刷新列表后重新选择") from exc


def list_instances(project, *, cancelled=lambda: False):
    rows = []
    deadline = time.monotonic() + 12
    for path in candidates(project):
        if cancelled() or time.monotonic() >= deadline or len(rows) >= LIMIT:
            break
        try:
            descriptor = descriptor_at(project, path)
            bridge, context = connect_descriptor(project, descriptor,
                deadline=min(deadline, time.monotonic() + 3), cancelled=cancelled)
            with bridge:
                rows.append(dict(id=path.stem, platform="CDNS-IC",
                    instance=context.instance_id, generation=context.generation,
                    project=str(project), version=str(context.snapshot.get("version", "")),
                    node=descriptor.get("node", socket.gethostname()),
                    job=descriptor.get("job", "")))
        except (OSError, EOFError, ValueError, RuntimeError, KeyError, TypeError):
            continue
    return rows
