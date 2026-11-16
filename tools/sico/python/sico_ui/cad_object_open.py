"""Open a missing CAD target and continue with the requested location."""

from __future__ import annotations


def _target_label(ref):
    target = ref["target"]
    return "/".join((target["library"], target["cell"], target["view_name"]))


def confirm_instance_open(owner, ref, candidate, label, confirm, locate):
    """Open a target on one explicitly selected instance after confirmation."""
    target_label = _target_label(ref)
    message = (
        "所选实例尚未打开目标窗口。\n\n是否在该实例以只读方式打开 "
        + target_label + " 并定位 " + ref["kind"] + " " + ref["name"] + "？"
    )
    try:
        accepted = confirm(
            owner.window, "在目标实例打开并定位", message, choice="只读打开并定位"
        )
    except Exception as exc:
        owner._warn("无法在目标实例打开并定位", str(exc))
        return
    if not accepted:
        owner._status("已取消打开目标实例")
        return
    _open_and_locate(
        owner, ref, candidate, label,
        open_title="无法在目标实例打开并定位",
        timeout_title="目标实例打开超时",
        timeout_detail="目标实例未及时响应。请重新点击链接或选择其他实例；不会自动重试。",
        opened_status="已在目标实例打开 " + target_label + "，正在定位对象",
        locate=locate,
    )


def confirm_current(owner, ref, confirm):
    """Open a target on the current instance after confirmation."""
    label = _target_label(ref)
    message = (
        "目标窗口未打开。\n\n是否以只读方式打开 "
        + label + " 并定位 " + ref["kind"] + " " + ref["name"] + "？"
    )
    try:
        accepted = confirm(
            owner.window, "打开并定位", message, choice="只读打开并定位"
        )
    except Exception as exc:
        owner._warn("无法打开并定位", str(exc))
        return
    if not accepted:
        owner._status("已取消打开目标窗口")
        return
    _open_and_locate(
        owner, ref, None, label,
        open_title="无法打开并定位",
        timeout_title="打开目标超时",
        timeout_detail="目标窗口打开超时。请确认 Virtuoso 仍可用后重试；不会自动重试。",
        opened_status="已打开 " + label + "，正在定位对象",
    )


def _open_and_locate(owner, ref, candidate, label, *, open_title,
                     timeout_title, timeout_detail, opened_status, locate=None):
    actions = getattr(owner.window, "actions", None)
    opener = getattr(actions, "open_object_target", None)
    if not callable(opener):
        owner._warn(open_title, "当前连接不支持打开目标窗口。")
        return
    request = owner._request + 1
    owner._request = request
    if owner._pending_provider is not None:
        owner._close(owner._pending_provider)
        owner._pending_provider = None
    try:
        future = (
            opener(ref) if candidate is None
            else opener(ref, candidate=candidate)
        )
    except Exception as exc:
        owner._warn(open_title, str(exc))
        return
    if future is None:
        owner._warn(open_title, "目标窗口打开请求未发送。")
        return
    scope = owner._scope()
    timer = owner._start_timer(
        request, None, timeout_title, timeout_detail, owner.OPEN_TIMEOUT_MS
    )

    def loaded(result):
        if request != owner._request or owner._closing():
            return
        owner._request += 1
        owner._stop_timer(timer)
        window_ref = result.get("window_ref") if isinstance(result, dict) else None
        if not isinstance(window_ref, str) or not window_ref.startswith("wctx_"):
            owner._warn(open_title, "Virtuoso 未返回可用的窗口引用。")
            return
        owner._status(opened_status)
        if candidate is None:
            owner._highlight(ref, window_ref=window_ref, allow_open=False)
        elif locate is not None:
            locate(owner, ref, candidate, window_ref, label)

    def failed(exc):
        if request != owner._request or owner._closing():
            return False
        owner._request += 1
        owner._stop_timer(timer)
        detail = str(exc).strip() or "目标窗口打开失败"
        owner._warn(open_title, detail[:300])
        return False

    def settled(*_args):
        owner._stop_timer(timer)

    try:
        owner.window.commands.watch(
            future, success=loaded, failure=failed, settled=settled, scope=scope
        )
    except Exception as exc:
        owner._request += 1
        owner._stop_timer(timer)
        owner._warn(open_title, str(exc))


__all__ = ["confirm_current", "confirm_instance_open"]
