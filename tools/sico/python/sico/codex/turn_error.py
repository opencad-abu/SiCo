"""Display bounded error classifications, never upstream bodies or credentials."""

_KINDS = {
    "contextWindowExceeded": "模型上下文已超出上限",
    "sessionBudgetExceeded": "模型会话预算已用尽",
    "usageLimitExceeded": "模型用量已达上限",
    "rateLimitExceeded": "模型请求触发限流",
    "serverOverloaded": "模型服务繁忙",
    "cyberPolicy": "模型策略拒绝了请求",
    "misalignmentPolicyViolation": "模型策略拒绝了请求",
    "internalServerError": "模型服务内部错误",
    "unauthorized": "模型凭据未获授权",
    "badRequest": "模型网关拒绝了请求参数",
    "threadRollbackFailed": "模型历史回退失败",
    "sandboxError": "Codex 执行环境失败",
    "other": "Codex 模型回合失败",
    "httpConnectionFailed": "模型网关 HTTP 请求失败",
    "responseStreamConnectionFailed": "无法连接模型响应流",
    "responseStreamDisconnected": "模型响应流在完成前断开",
    "responseTooManyFailedAttempts": "模型连接失败次数已达上限",
}


def failure_message(error=None, *, wrapper=None, gateway=None, tool_format="native"):
    """Use the pinned app-server schema plus our own wrapper diagnostics only."""
    info = error.get("codexErrorInfo") if isinstance(error, dict) else None
    kind, status = None, None
    if isinstance(info, str) and info in _KINDS:
        kind = info
    elif isinstance(info, dict):
        for name in _KINDS:
            if name in info and isinstance(info[name], dict):
                kind = name
                code = info[name].get("httpStatusCode")
                if type(code) is int and 100 <= code <= 599:
                    status = code
                break
    if status is None:
        for source in (wrapper, gateway):
            observed = getattr(source, "http_status", None)
            if type(observed) is int and 400 <= observed <= 599:
                status = observed
                break
    detail = _KINDS.get(kind, "Codex 模型回合失败")
    if status is not None:
        detail += f"（HTTP {status}）"
    if kind:
        detail += f" [{kind}]"
    mode = "flat" if tool_format == "flat" else "native"
    detail += f"；工具格式：{mode}。"
    # ResponsesWrapper creates these diagnostics locally from fixed messages;
    # upstream error.message/additionalDetails are deliberately never copied.
    if wrapper is not None and wrapper.diagnostic:
        detail += " 兼容层：" + wrapper.diagnostic + "。"
    if gateway is not None and gateway.diagnostic:
        detail += " LSF 网关：" + gateway.diagnostic + "。"
    if status in (401, 403) or kind == "unauthorized":
        detail += " 请检查模型凭据及网关访问权限。"
    elif status == 429 or kind in ("usageLimitExceeded", "rateLimitExceeded"):
        detail += " 请检查网关配额和限流。"
    elif status in (400, 404, 422) or kind == "badRequest":
        detail += " 请核对 Responses 地址、模型名及网关支持的请求字段/工具类型。"
    else:
        detail += " 请按错误分类核对网关或 Codex 会话日志。"
    return detail
