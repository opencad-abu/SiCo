"""Route native notifications in precedence order, preserving stream draining."""


def route(event, *, lock, goals, audit, names, children, history, active, turn_id=None, consuming=False):
    with lock:
        if goals.notification(event, **({"consuming": True} if consuming else {})):
            return True, consuming
        if audit is not None and audit.resolved(event):
            return True, False
        if names.notification(event):
            return True, consuming
    if children.notification(event):
        return True, consuming
    if consuming:
        handled = history.notification(event, turn_id)
        return bool(handled), bool(handled)
    return not active and history.notification(event), False
