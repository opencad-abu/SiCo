"""Read-only bridge queries for the remote desktop's @ object picker."""

from __future__ import annotations

from ..transport.framing import ProtocolError


class RemoteObjectError(ValueError):
    """A bounded object-query failure carrying its stable native code."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class RemoteObjectSource:
    """A query-only facade; it cannot create, open, or mutate a target."""

    def __init__(self, api, address, token):
        self._api = api
        self._address = address
        self._token = token
        self._closed = False

    def _request(self, operation, *args):
        if self._closed:
            raise ValueError("对象读取器已关闭")
        self._api.handle(self._token)
        epoch = self._api._queries.epoch

        def validate_connection():
            self._api.handle(self._token)
            if self._api._queries.epoch != epoch:
                raise ValueError("查询连接已更换，请重新读取对象")

        def params():
            if self._closed:
                raise ValueError("对象读取器已关闭")
            validate_connection()
            return {
                "address": self._address.record(),
                "operation": operation,
                "args": list(args),
            }

        def publish(row):
            if self._closed:
                raise ValueError("对象读取器已关闭")
            validate_connection()
            if not isinstance(row, dict):
                raise ProtocolError("对象读取结果无效")
            if row.get("ok") is False:
                code = row.get("code")
                if not isinstance(code, str) or not code:
                    raise ProtocolError("对象读取错误缺少代码")
                raise RemoteObjectError(code, str(row.get("message") or code)[:512])
            return row

        return self._api._queries.request(
            "object_context", params, publish, validate=validate_connection
        )

    def selection(self, window_ref, offset=0, max_items=50):
        return self._request("selection", str(window_ref), offset, max_items)

    def objects(self, window_ref, kind="all", query="", offset=0, max_items=50):
        return self._request(
            "objects", str(window_ref), kind, str(query), offset, max_items
        )

    def windows(self, ref):
        return self._request("windows", ref)

    def window_list(self):
        return self._request("window_list")

    def highlight(self, ref, window_ref=None):
        args = [ref]
        if window_ref is not None:
            args.append(str(window_ref))
        return self._request("highlight", *args)

    def close(self):
        self._closed = True


__all__ = ["RemoteObjectError", "RemoteObjectSource"]
