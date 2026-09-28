"""Authorize existing batch admission/cancellation against the current session lease."""

from ..core.contracts import BoundContext
from ..transport.framing import ProtocolError
from .background_config import captured_backend
from .background_environment import capture_environment


def background_command(workspace, control, address, wire, operation, args, kwargs):
    if operation == 'submit_background':
        if (len(args) != 2 or set(kwargs) != {'origin'}
                or not isinstance(kwargs['origin'], BoundContext)):
            raise ProtocolError('Background submission requires captured design')
    elif len(args) != 1 or kwargs:
        raise ProtocolError('Background cancellation requires its original job ID')

    def execute(manager):
        with control.guard(address) as lease:
            controller = workspace.controllers[address.session.session_id]
            with controller._lock:
                control.require(lease, wire.connection, wire.control)
                options = dict(kwargs)
                if operation == 'submit_background':
                    if kwargs['origin'] != controller.frontend_view.context:
                        raise ValueError('Background design no longer matches the session')
                    if controller.recovery is not None and controller.recovery.blocked:
                        raise ValueError('Reconcile the recovered session before new execution')
                    deps = workspace.owner.dependencies_for(controller.session_id)
                    options.update(environment=capture_environment(deps.environment,
                        credential=(deps.provider_config or {}).get('api_key_env', 'SICO_API_KEY')),
                        backend=captured_backend(workspace.root / 'background', deps.background),
                        operation_id=wire.operation)
                return getattr(manager, operation)(
                    *args, session_id=controller.session_id, **options)

    # Admission and takeover share the gate on the background worker thread.
    return workspace.background.authorized(execute).result()
