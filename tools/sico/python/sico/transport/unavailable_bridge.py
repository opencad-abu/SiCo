"""Historical design provenance without a live host or executable design transport."""

from .methods import QueryUnavailable


class UnavailableBridge:
    descriptor = {}

    def register_target(self, context):
        pass  # A historical reference does not acquire any host resource.

    def release_target(self, context):
        pass

    def close(self):
        pass

    def get_context(self, context):
        return self.read(context, "get_context")

    def read(self, *args, **kwargs):
        raise QueryUnavailable("router_unavailable",
            "原 Virtuoso 连接已断开，当前无法使用设计工具；可以继续对话。")

    circuit_call = read
    assistant_call = read
