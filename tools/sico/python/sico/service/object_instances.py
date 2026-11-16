"""Read-only object lookup and view location across project Virtuoso instances."""

from __future__ import annotations

import time
import uuid

from ..transport.host_discovery import connect_candidate, list_instances
from .targets import BridgeTargetProvider, TargetBridgeError


class _BridgeBroker:
    def __init__(self, bridge):
        self.bridge = bridge

    def circuit_call(self, context, params):
        return self.bridge.circuit_call(context, params)


def _provider(project, candidate, *, cancelled):
    bridge, context = connect_candidate(project, candidate, cancelled=cancelled)
    session_id = "cross_" + uuid.uuid4().hex
    try:
        bridge.register_session(session_id, context)
    except Exception:
        bridge.close()
        raise
    return bridge, context, BridgeTargetProvider(
        _BridgeBroker(bridge), context, session_id=session_id
    )


def _close(bridge, provider):
    if bridge is None:
        return
    session_id = getattr(provider, "session_id", None) if provider is not None else None
    if session_id:
        try:
            bridge.release_session(session_id)
        except Exception:
            pass
    bridge.close()


def instance_windows(project, ref, current_instance, *, cancelled=lambda: False):
    """Return bounded target windows found on other project instances."""
    rows = []
    deadline = time.monotonic() + 20
    for candidate in list_instances(project, cancelled=cancelled):
        if time.monotonic() >= deadline:
            break
        if candidate["instance"] == current_instance:
            continue
        bridge = None
        provider = None
        try:
            bridge, context, provider = _provider(
                project, candidate["id"], cancelled=cancelled
            )
            try:
                windows = provider.windows(ref).get("windows", [])
            except TargetBridgeError as exc:
                if exc.code != "target_not_open":
                    raise
                windows = []
            selected = [
                {
                    "window_ref": window["window_ref"],
                    "window": window.get("window") or "",
                    "title": window.get("title") or "",
                }
                for window in windows[:8] if window.get("window_ref")
            ]
            rows.append({
                "candidate": candidate["id"],
                "instance": context.instance_id,
                "generation": context.generation,
                "windows": selected,
            })
        except (OSError, EOFError, ValueError, RuntimeError, KeyError, TypeError):
            continue
        finally:
            _close(bridge, provider)
    return rows[:8]


def _open_target(project, candidate, ref, *, cancelled):
    bridge = None
    provider = None
    try:
        bridge, _context, provider = _provider(
            project, candidate, cancelled=cancelled
        )
        return provider.open_target({
            **ref["target"], "read_only": True,
        })
    except TargetBridgeError as exc:
        raise ValueError(str(exc)) from exc
    finally:
        _close(bridge, provider)


def highlight_instance(project, candidate, ref, window_ref, *, cancelled=lambda: False):
    """Locate one object through an explicitly selected project instance."""
    bridge = None
    provider = None
    try:
        bridge, context, provider = _provider(project, candidate, cancelled=cancelled)
        result = provider.highlight(ref, window_ref)
        return {
            "candidate": candidate,
            "instance": context.instance_id,
            "generation": context.generation,
            "result": result,
        }
    except TargetBridgeError as exc:
        raise ValueError(str(exc)) from exc
    finally:
        _close(bridge, provider)


def open_target_on_instance(project, instance_id, ref, *, cancelled=lambda: False):
    """Open a missing target through a bounded session on this instance."""
    for row in list_instances(project, cancelled=cancelled):
        if row["instance"] != instance_id:
            continue
        return _open_target(project, row["id"], ref, cancelled=cancelled)
    raise ValueError("当前实例已不可用，请重新打开窗口")


def open_target_on_candidate(project, candidate, ref, *, cancelled=lambda: False):
    """Open a missing target on one explicitly selected project instance."""
    if not isinstance(candidate, str) or not candidate:
        raise ValueError("无效的目标实例")
    return _open_target(project, candidate, ref, cancelled=cancelled)


__all__ = [
    "highlight_instance",
    "instance_windows",
    "open_target_on_candidate",
    "open_target_on_instance",
]
