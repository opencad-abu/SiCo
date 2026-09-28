"""Asynchronous net analysis actions mixed into the main window."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .workers import QueryController


def _summary_query(index_path: Path, net: int | str) -> Any:
    from rcepy.dspf.repository import DspfRepository

    with DspfRepository(index_path) as repository:
        return repository.net_summary(net)


def _path_query(index_path: Path, net: int | str, start: str, end: str) -> Any:
    from rcepy.dspf.repository import DspfRepository

    with DspfRepository(index_path) as repository:
        return repository.resistance_path(net, start, end)


def _network_query(index_path: Path, net: int | str) -> tuple[Any, Any]:
    from rcepy.dspf.network_layout import layout_rc_network
    from rcepy.dspf.repository import DspfRepository

    with DspfRepository(index_path) as repository:
        network = repository.rc_network(net, include_capacitors=False)
    layout = layout_rc_network(network) if network.status == "ok" else None
    return network, layout


def _integrity_query(
    index_path: Path, net: int | str, short_threshold_ohm: float
) -> Any:
    from rcepy.dspf.repository import DspfRepository

    with DspfRepository(index_path) as repository:
        return repository.resistance_integrity(
            net, short_threshold_ohm=short_threshold_ohm
        )


class AnalysisActionsMixin:
    def _configure_analysis_workers(self) -> None:
        self.summary_queries = QueryController(self)
        self.summary_queries.resultReady.connect(self._summary_ready)
        self.summary_queries.failed.connect(self._show_error)
        self.path_queries = QueryController(self)
        self.path_queries.busyChanged.connect(self.ui.path_pane.set_busy)
        self.path_queries.resultReady.connect(self.ui.path_pane.set_result)
        self.path_queries.failed.connect(self._path_failed)
        self.network_queries = QueryController(self)
        self.network_queries.busyChanged.connect(self.ui.network_pane.set_busy)
        self.network_queries.resultReady.connect(self._network_ready)
        self.network_queries.failed.connect(self._network_failed)
        self.integrity_queries = QueryController(self)
        self.integrity_queries.busyChanged.connect(
            self.ui.network_pane.set_integrity_busy
        )
        self.integrity_queries.resultReady.connect(
            self.ui.network_pane.set_integrity_result
        )
        self.integrity_queries.failed.connect(self.ui.network_pane.set_integrity_error)
        self.highlight_queries = QueryController(self)
        self.highlight_queries.busyChanged.connect(self._highlight_busy_changed)
        self.highlight_queries.resultReady.connect(self._highlight_regions_ready)
        self.highlight_queries.failed.connect(self._highlight_query_failed)
        self._highlight_busy = False
        self.ui.network_pane.integrityRequested.connect(
            self._check_network_integrity
        )
        self._network_pending_key = None
        self._network_loaded_key = None

    def _queue_summary(self) -> None:
        if self.index_path is None or self.current_net is None:
            return
        self.ui.summary.set_summary(
            {"name": self.current_net_name, "node_count": "Loading..."}
        )
        path, net = self.index_path, self.current_net
        self.summary_queries.submit(lambda: _summary_query(path, net))

    def _summary_ready(self, summary: Any) -> None:
        self.ui.summary.set_summary(self._analysis_dict(summary))

    def _analyze_path(self, start: str, end: str) -> None:
        if not self.index_path or self.current_net is None or not start or not end:
            self.ui.path_pane.set_result({"error": "Select a net and enter both nodes"})
            return
        path, net = self.index_path, self.current_net
        self.path_queries.submit(lambda: _path_query(path, net, start, end))

    def _path_failed(self, message: str) -> None:
        self.ui.path_pane.set_result({"error": message})

    def _network_key(self) -> tuple[Path, int | str] | None:
        if self.index_path is None or self.current_net is None:
            return None
        return self.index_path, self.current_net

    def _queue_network_if_visible(self) -> None:
        key = self._network_key()
        if (
            key is None
            or self.ui.tabs.currentWidget() is not self.ui.network_pane
            or key in (self._network_pending_key, self._network_loaded_key)
        ):
            return
        self._network_pending_key = key
        path, net = key
        self.network_queries.submit(lambda: _network_query(path, net))

    def _network_tab_changed(self, _index: int) -> None:
        self._queue_network_if_visible()

    def _network_ready(self, result: tuple[Any, Any]) -> None:
        self._network_loaded_key = self._network_pending_key
        self._network_pending_key = None
        self.ui.network_pane.set_network(*result)

    def _network_failed(self, message: str) -> None:
        self._network_pending_key = None
        self.ui.network_pane.set_error(message)

    def _check_network_integrity(self, short_threshold_ohm: float) -> None:
        if self.index_path is None or self.current_net is None:
            self.ui.network_pane.set_integrity_error("Select a net and open an index")
            return
        path, net = self.index_path, self.current_net
        threshold = max(0.0, float(short_threshold_ohm))
        self.integrity_queries.submit(
            lambda: _integrity_query(path, net, threshold)
        )

    def _invalidate_network(self) -> None:
        self.network_queries.invalidate()
        self.integrity_queries.invalidate()
        self.highlight_queries.invalidate()
        self._network_pending_key = None
        self._network_loaded_key = None
        self.ui.network_pane.clear()

    def _invalidate_path(self) -> None:
        self.path_queries.invalidate()
        self.ui.path_pane.clear_result()

    def _invalidate_analysis(self) -> None:
        self.summary_queries.invalidate()
        self._invalidate_network()
        self._invalidate_path()

    def _shutdown_analysis(self) -> None:
        self.summary_queries.shutdown()
        self.network_queries.shutdown()
        self.integrity_queries.shutdown()
        self.highlight_queries.shutdown()
        self.path_queries.shutdown()


__all__ = ["AnalysisActionsMixin"]
