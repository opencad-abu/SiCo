"""One user action to reopen a conversation and obtain its existing write authority."""

from contextlib import ExitStack

from ..core.contracts import identifier
from .continuation_host import current_host
from .frontend_attach import session_record
from .service_protocol import exact_fields
from .service_session_dto import SessionAddress


def continue_session(hub, wire, params):
    exact_fields(params, {"session_id", "source_session_id"} if "source_session_id" in params
                 else {"session_id"})
    key = identifier(params["session_id"])
    preferred = params.get("source_session_id")
    if preferred is not None:
        identifier(preferred)
    with hub.owner._mutation, ExitStack() as pending:
        controller = hub.owner.controllers.get(key)
        candidate = None
        if controller is not None:
            with controller._lock:
                idle = (not controller.busy and not controller.closing
                        and not controller._shutdown.is_set()
                        and not getattr(controller.loop, "_inflight", ()))
            if idle:
                candidate = current_host(hub.owner, preferred)
                if candidate is not None:
                    pending.enter_context(candidate[0])
                    descriptor = hub.owner.dependencies_for(key).bridge_descriptor
                    if all(descriptor.get(k) == candidate[0].descriptor.get(k)
                           for k in ("bridge_id", "router_id")):
                        candidate[0].close()
                        candidate = None
                if candidate is not None:
                    old = SessionAddress.from_record(session_record(controller, hub.descriptor)["address"])
                    with hub.control.guard(old):
                        hub.control.continue_session(wire.connection, old)
                        with controller._lock:
                            if controller.busy or getattr(controller.loop, "_inflight", ()):
                                raise ValueError("当前任务仍在执行，请等待任务结束")
                            hub.owner.creation.retain(controller, hub.owner.dependencies_for(key))
                            controller.request_close()
                        hub.owner.close_session(key)
                    controller = None
        if controller is None:
            controller = hub.recovery.continue_session(wire.connection, wire.operation, key,
                                                     preferred=preferred, selected=candidate)
            pending.pop_all()  # The published runtime owns the selected bridge.
        address = SessionAddress.from_record(session_record(controller, hub.descriptor)["address"])
        with hub.control.guard(address):
            granted = hub.control.continue_session(wire.connection, address)
            with controller._lock:
                if controller.recovery is not None:
                    controller.recovery.continue_chat()
            return dict(session=session_record(controller, hub.descriptor), control=granted.record())
