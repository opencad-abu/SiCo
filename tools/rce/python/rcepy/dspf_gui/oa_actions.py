"""OA bridge actions mixed into the DSPF main window."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .bridge import StdioBridge


def _dspf_name_from_oa(name: str) -> str:
    """Return the conventional DSPF spelling for an OA bus name."""
    return name.replace("<", "[").replace(">", "]")


def _highlight_query(
    index_path: Path, net: int | str, name: str,
) -> tuple[str, tuple[tuple[float, float, float, float], ...]]:
    from rcepy.dspf.highlight import resistance_highlight_regions
    from rcepy.dspf.repository import DspfRepository

    with DspfRepository(index_path) as repository:
        regions = resistance_highlight_regions(repository, net)
    return name, regions


class OaBridgeMixin:
    bridge: StdioBridge | None

    def _enable_bridge(self) -> None:
        self.bridge = StdioBridge(parent=self)
        self.bridge.requestReceived.connect(self._bridge_request)
        self.bridge.eventReceived.connect(self._bridge_event)
        self.bridge.responseReceived.connect(self._bridge_response)
        self.bridge.protocolError.connect(lambda text: self._set_status(f"Bridge: {text}"))
        self.bridge.disconnected.connect(self._bridge_disconnected)

    def _bridge_disconnected(self) -> None:
        self.bridge = None
        self._pending.clear()
        self.highlight_queries.invalidate()
        self._set_status("cdns-ipc disconnected")
        self._update_actions()
        if getattr(self, "_bridge_enabled", False):
            self.close()

    def _highlight_current_net(self) -> None:
        if (
            self.bridge and self.current_net_name and self.current_net is not None
            and self.index_path is not None
        ):
            path, net, name = self.index_path, self.current_net, self.current_net_name
            self.highlight_queries.submit(
                lambda: _highlight_query(path, net, name)
            )

    def _highlight_regions_ready(
        self,
        result: tuple[str, tuple[tuple[float, float, float, float], ...]],
    ) -> None:
        name, regions = result
        if not self.bridge or name != self.current_net_name:
            return
        request_id = self.bridge.request(
            "oa.highlight_net", {"name": name, "regions": regions}
        )
        self._pending[request_id] = "highlight"

    def _highlight_query_failed(self, message: str) -> None:
        self._show_error(f"Cannot prepare R highlight regions: {message}")

    def _highlight_busy_changed(self, busy: bool) -> None:
        self._highlight_busy = busy
        self._update_actions()

    def _request_oa_selection(self) -> None:
        if self.bridge:
            request_id = self.bridge.request("oa.current_net")
            self._pending[request_id] = "selection"

    def _bridge_request(self, message: dict[str, Any]) -> None:
        assert self.bridge is not None
        name = message["params"]["name"]
        selected_name = self._select_oa_net_name(name)
        if selected_name is not None:
            self.bridge.respond(message["id"], result={"name": selected_name})
        else:
            self.bridge.respond(
                message["id"],
                error={"code": "net_not_found", "message": self._last_select_error},
            )

    def _bridge_event(self, message: dict[str, Any]) -> None:
        self._select_oa_net_name(message["params"]["name"])

    def _bridge_response(self, message: dict[str, Any]) -> None:
        request_id = message["id"]
        operation = self._pending.pop(request_id, None)
        if operation is None:
            self._set_status(f"Bridge: unmatched response id {request_id}")
            return
        if not message["ok"]:
            self._show_error(message["error"]["message"])
        elif operation == "selection":
            name = message.get("result", {}).get("name")
            if name:
                self._select_oa_net_name(name)
        else:
            result = message.get("result", {})
            count = result.get("figure_count")
            scope = result.get("scope")
            if scope == "resistance_regions":
                self._set_status(f"Layout R-region highlight updated ({count} shapes)")
            elif scope == "full_net_fallback":
                self._set_status(
                    f"Layout full-net highlight updated ({count} shapes; "
                    "DSPF R regions did not intersect OA geometry)"
                )
            else:
                self._set_status(f"Layout full-net highlight updated ({count} shapes)")

    def _select_oa_net_name(self, name: str) -> str | None:
        errors = []
        for candidate in dict.fromkeys((name, _dspf_name_from_oa(name))):
            if self.select_net_by_name(candidate):
                return candidate
            errors.append(self._last_select_error)
        self._last_select_error = errors[-1]
        return None


__all__ = ["OaBridgeMixin"]
