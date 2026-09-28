"""Route captured Ask inputs by live design bindings on the command worker."""

from dataclasses import dataclass

from ..core.contracts import BoundContext
from ..transport.bindings import project_key, resource_keys
from ..transport.targets import submission


def design_keys(context):
    """A window handle alone is not evidence that two designs are related."""
    return frozenset(key for key in resource_keys(context)
                     if key[2] in {"cellview", "path", "ade"})


def cell_keys(keys):
    """Views and named variants of one library/cell share a conversation."""
    return frozenset(key[:-1] for key in keys if key[2] in {"cellview", "path"})


def validate_source(context):
    if context.snapshot.get("valid") is False:
        raise ValueError("快速输入的设计来源已失效")
    cellview = context.snapshot.get("cellview")
    if cellview is not None and (not isinstance(cellview, dict) or not all(
        isinstance(cellview.get(key), str) and cellview[key] for key in ("lib", "cell", "view")
    )):
        raise ValueError("快速输入缺少完整的 lib/cell/view")


def select_destination(preferred, controllers, context):
    """One design-affinity policy shared by service admission and the legacy desktop."""
    keys = design_keys(context)
    if not keys:
        return preferred
    related = None
    candidates = ([preferred] if preferred is not None else []) + [
        item for item in controllers if item is not preferred]
    for controller in candidates:
        affinity = QuickInputRouting._affinity(controller, context, keys)
        if affinity == 2:
            return controller
        if affinity == 1 and related is None:
            related = controller
    return related


@dataclass(frozen=True)
class QuickInputReceipt:
    controller: object
    created: bool
    accepted: bool


class QuickInputRouting:
    def __init__(self, sessions):
        self.sessions = sessions
        # Pin even a failed acceptance to its chosen session: a lost receipt
        # or a page switch must never duplicate the same request elsewhere.
        self.destinations = {}

    def accept(self, preferred, message):
        context = submission(message)
        sessions = self.sessions
        if sessions.controllers.get(preferred.session_id) is not preferred:
            raise ValueError("快速输入所属的会话已变化")
        with preferred._lock:
            preferred._check_input(message["text"])
            anchor = preferred.current
            if ((context.instance_id, context.generation)
                    != (anchor.instance_id, anchor.generation)
                    or project_key(context.snapshot) != project_key(anchor.snapshot)):
                raise ValueError("快速输入属于其他 Virtuoso 实例或工程")
        validate_source(context)

        destination = self.destinations.get(message["id"])
        created = False
        if destination is None:
            destination = select_destination(preferred, sessions.controllers.values(), context)
            if destination is None:
                broker = sessions.broker or preferred.bindings.broker
                if broker is None:
                    raise ValueError("快速输入的目标连接不可用")
                broker.register_target(context)
                destination = sessions.create(context)
                self.destinations[message["id"]] = destination
                if destination.bindings.broker is None:
                    destination.attach_targets(broker)
                created = True
            self.destinations[message["id"]] = destination
        if sessions.controllers.get(destination.session_id) is not destination:
            raise ValueError("该快速输入的原会话已不可用，请核对接收记录")
        if sessions.worker._closing:
            destination.request_close()
            raise ValueError("Silicon Copilot 正在关闭，快速输入未接收")
        accepted = destination.accept(message)
        return QuickInputReceipt(destination, created, accepted)

    @staticmethod
    def _affinity(controller, context, keys):
        with controller._lock:
            if (controller.closing or controller.fault or controller._shutdown.is_set()
                    or (controller.current.instance_id, controller.current.generation)
                    != (context.instance_id, context.generation)
                    or project_key(controller.current.snapshot) != project_key(context.snapshot)):
                return 0
            # The bridge owns membership and invalidation. Do not route from
            # the UI's delayed snapshot or from a former task's cellview.
            controller.bindings.events.refresh()
            state = controller.bindings.events.state or {}
            invalidated = state.get("invalidated", {})
            targets = [target for target in controller.bindings.events.targets.values()
                       if target.target_id not in invalidated
                       and target.snapshot.get("valid") is not False]
            targets += [BoundContext.from_record(row["context"])
                        for row in state.get("reservations", [])]
            known = set().union(*(design_keys(target) for target in targets))
            # Honor an existing owner before preferring a related cell in the
            # current page. Earlier explicit sessions may own different views.
            if keys & known:
                return 2
            return 1 if cell_keys(keys) & cell_keys(known) else 0
