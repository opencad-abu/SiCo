"""Open a design session from a revalidated platform candidate."""

from contextlib import ExitStack

from ..transport.host_discovery import connect_candidate
from .service_protocol import exact_fields
from .service_values import name


def open_platform(attach, wire, params):
    exact_fields(params, {"session_id", "platform", "candidate", "provider_config", "environment"})
    if params["platform"] != "CDNS-IC":
        raise ValueError("此 IC 平台暂未支持")
    name(params["session_id"])
    attach._dependencies(params)
    attach._new(params["session_id"])
    with ExitStack() as pending:
        bridge, bound = connect_candidate(attach.owner.project, params["candidate"],
                                         cancelled=wire.closed.is_set)
        pending.enter_context(bridge)
        # Model credentials belong to the desktop; EDA tools inherit the selected host.
        from cadenv import VIRTUOSO_ENV_NAMES
        from .background_environment import SNAPSHOT_NAMES

        environment = {key: value for key, value in params["environment"].items()
                       if key not in SNAPSHOT_NAMES}
        eda = bridge.platform_environment(cancelled=wire.closed.is_set)
        environment.update(eda)
        for key in VIRTUOSO_ENV_NAMES:
            environment["SICO_VIRTUOSO_" + key] = eda.get(key, "")
        with attach.owner._mutation:
            if wire.closed.is_set():
                raise ValueError("连接等待已结束")
            controller = attach._existing_target(dict(context=bound, bridge=bridge.descriptor))
            if controller is not None:
                return controller, False
            controller = attach.owner.open(params["session_id"], bridge.descriptor, bound,
                provider_config=params["provider_config"], environment=environment,
                bridge=bridge, new_only=True)
            pending.pop_all()
            return controller, True
