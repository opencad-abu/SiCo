"""Release unpublished construction or drain one owned session resource bundle."""


def close_partial(controller, loop, journal, bridge, *, wait_timeout=0):
    if controller is not None:
        try:
            controller.close()
        except BaseException:
            pass
        try:
            if wait_timeout is None:
                controller.wait()
            else:
                controller.wait(wait_timeout)
        except BaseException:
            pass
    if loop is not None and controller is None:
        try:
            loop.close()
        except BaseException:
            pass
    if journal is not None:
        try:
            journal.close()
        except BaseException:
            pass
    if bridge is not None:
        try:
            bridge.close()
        except BaseException:
            pass


def close_bundle(bundle, *, bridge=True):
    if bundle.health is not None:
        bundle.health.close()
        bundle.health.wait()
    controller = bundle.controller
    controller.request_close()
    controller.close()
    controller.wait()
    changed = getattr(controller.loop, "_tools_changed", None)
    if changed is not None:
        with changed:
            changed.wait_for(lambda: not controller.loop._inflight)
    if bridge:
        bundle.bridge.close()
        bundle.resources.close()
    bundle.journal.close()
