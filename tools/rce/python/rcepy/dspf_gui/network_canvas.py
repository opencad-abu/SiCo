"""Batched, interactive rendering for a resistance network."""

from __future__ import annotations

import math
from typing import Any

from PyQt5.QtCore import QPoint, QPointF, QRectF, QTimer, Qt
from PyQt5.QtGui import (
    QColor,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QWheelEvent,
)
from PyQt5.QtWidgets import QGraphicsItem, QGraphicsScene, QGraphicsView, QToolTip

from .network_format import (
    format_node_label,
    format_tooltip,
)


_COLORS = ("#2166ac", "#258f8b", "#65a64a", "#c5bd32", "#ed9b27", "#df694c", "#b42318")


def _distance(point: QPointF, first: QPointF, second: QPointF) -> float:
    dx, dy = second.x() - first.x(), second.y() - first.y()
    if dx == 0 and dy == 0:
        return math.hypot(point.x() - first.x(), point.y() - first.y())
    ratio = ((point.x() - first.x()) * dx + (point.y() - first.y()) * dy) / (
        dx * dx + dy * dy
    )
    ratio = min(1.0, max(0.0, ratio))
    return math.hypot(
        point.x() - first.x() - ratio * dx, point.y() - first.y() - ratio * dy
    )


class _NetworkItem(QGraphicsItem):
    def __init__(self, network: Any, layout: Any) -> None:
        super().__init__()
        self.network, self.layout = network, layout
        self.mode, self.labels = "r", False
        self.nodes = {node.id: node for node in network.nodes}
        self.points = {
            key: QPointF(value[0], -value[1]) for key, value in layout.positions.items()
        }
        self.ground_points = {
            key: QPointF(value[0], -value[1])
            for key, value in getattr(layout, "ground_positions", {}).items()
        }
        self.r_node_ids = {
            node_id
            for part in network.resistors
            for node_id in (part.node1_id, part.node2_id)
        }
        self._r_values = [part.value for part in network.resistors if part.value > 0]
        self._c_values = [part.value for part in network.capacitors if part.value > 0]
        self._c_values += [
            node.ground_capacitance
            for node in network.nodes
            if node.ground_capacitance > 0
        ]
        self._r_range = (min(self._r_values), max(self._r_values)) if self._r_values else None
        self._c_range = (min(self._c_values), max(self._c_values)) if self._c_values else None
        self.r_paths = [QPainterPath() for _ in _COLORS]
        self.c_paths = [QPainterPath() for _ in _COLORS]
        self.zero_path, self.warning_path = QPainterPath(), QPainterPath()
        self.c_zero_path, self.c_warning_path = QPainterPath(), QPainterPath()
        self.rings: list[tuple[QPointF, str]] = []
        self.diamonds: list[QPointF] = []
        self.drawable_nodes = [
            node
            for node in network.nodes
            if node.kind != "ground" and node.id in self.points
        ]
        self.r_drawable_nodes = [
            node
            for node in self.drawable_nodes
            if not node.external or node.id in self.r_node_ids
        ]
        self.visible_nodes = [
            node for node in network.nodes
            if not node.external and node.kind != "ground" and node.id in self.points
        ]
        self.ground_nodes = [
            node for node in self.visible_nodes if node.ground_capacitor_count
        ]
        ground_values = [
            node.ground_capacitance
            for node in self.ground_nodes if node.ground_capacitance > 0
        ]
        self._ground_range = (
            (min(ground_values), max(ground_values)) if ground_values else None
        )
        self.marker_scale = min(
            1.0, max(0.2, math.sqrt(400.0 / max(len(self.ground_nodes), 1)))
        )
        self.fit_rect = self._node_bounds(
            self.r_drawable_nodes, self.ground_points.values()
        )
        self.all_fit_rect = self._node_bounds(self.drawable_nodes)
        self.node_hits = [(node, self.points[node.id]) for node in self.drawable_nodes]
        self.ground_hits: list[tuple[Any, QPointF]] = []
        self.hits: list[tuple[str, Any, QPointF, QPointF]] = []
        self._build_resistors()
        self._build_capacitors()
        self._bounds = self._drawing_bounds()

    def _node_bounds(self, nodes: list[Any], extra=()) -> QRectF:
        points = [self.points[node.id] for node in nodes]
        points.extend(extra)
        if not points:
            x1, y1, x2, y2 = self.layout.bounds
            return QRectF(x1, -y2, x2 - x1, y2 - y1)
        xs, ys = [point.x() for point in points], [point.y() for point in points]
        return QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))

    def node_is_visible(self, node: Any) -> bool:
        return self.mode != "r" or not node.external or node.id in self.r_node_ids

    def _bin(self, value: float, limits: tuple[float, float] | None) -> int:
        if limits is None or value <= 0 or not math.isfinite(value):
            return 0
        low, high = limits
        if low == high:
            return len(_COLORS) // 2
        ratio = (math.log(value) - math.log(low)) / (math.log(high) - math.log(low))
        return min(len(_COLORS) - 1, max(0, int(ratio * len(_COLORS))))

    @staticmethod
    def _line(path: QPainterPath, first: QPointF, second: QPointF) -> None:
        path.moveTo(first)
        path.lineTo(second)

    def _build_resistors(self) -> None:
        for resistor in self.network.resistors:
            first, second = self._resistor_points(resistor)
            if first is None or second is None:
                continue
            self.hits.append(("resistor", resistor, first, second))
            coincident = resistor.node1_id == resistor.node2_id or first == second
            if coincident:
                color = (
                    "#b42318"
                    if resistor.value < 0 or resistor.cross_net
                    else "#6b7280"
                    if resistor.value == 0
                    else _COLORS[self._bin(resistor.value, self._r_range)]
                )
                self.rings.append((first, color))
                continue
            target = (
                self.warning_path
                if resistor.value < 0
                or resistor.cross_net
                or not math.isfinite(resistor.value)
                else self.zero_path
                if resistor.value == 0
                else self.r_paths[self._bin(resistor.value, self._r_range)]
            )
            self._line(target, first, second)
            if resistor.value == 0:
                self.diamonds.append((first + second) / 2)

    def _resistor_points(self, resistor: Any) -> tuple[QPointF | None, QPointF | None]:
        first_node = self.nodes.get(resistor.node1_id)
        second_node = self.nodes.get(resistor.node2_id)
        first = self.points.get(resistor.node1_id)
        second = self.points.get(resistor.node2_id)
        ground = self.ground_points.get(resistor.id)
        if first_node is not None and first_node.kind == "ground" and ground is not None:
            first = ground
            self.ground_hits.append((first_node, ground))
        if second_node is not None and second_node.kind == "ground" and ground is not None:
            second = ground
            self.ground_hits.append((second_node, ground))
        return first, second

    def _build_capacitors(self) -> None:
        for capacitor in self.network.capacitors:
            if capacitor.kind != "coupling":
                continue
            first = self.points.get(capacitor.node1_id)
            second = self.points.get(capacitor.node2_id)
            if first is None or second is None:
                continue
            target = (
                self.c_warning_path
                if capacitor.value < 0 or not math.isfinite(capacitor.value)
                else self.c_zero_path
                if capacitor.value == 0
                else self.c_paths[self._bin(capacitor.value, self._c_range)]
            )
            self._line(target, first, second)
            self.hits.append(("capacitor", capacitor, first, second))

    def _drawing_bounds(self) -> QRectF:
        points = [point for _node, point in (*self.node_hits, *self.ground_hits)]
        if not points:
            return QRectF(self.fit_rect).adjusted(-1, -1, 1, 1)
        xs, ys = [point.x() for point in points], [point.y() for point in points]
        rect = QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))
        span = max(rect.width(), rect.height(), 1.0)
        return rect.adjusted(-span * 0.08, -span * 0.08, span * 0.08, span * 0.08)

    def boundingRect(self) -> QRectF:
        return self._bounds

    def paint(self, painter: QPainter, _option, _widget=None) -> None:
        scale = max(abs(painter.worldTransform().m11()), 1e-12)
        if self.mode in {"r", "rc"}:
            for index, path in enumerate(self.r_paths):
                color = QColor(_COLORS[index])
                color.setAlpha(150 if self.mode == "rc" else 230)
                pen = QPen(color, 1.0 + 1.4 * index / (len(_COLORS) - 1))
                pen.setCosmetic(True)
                painter.setPen(pen)
                painter.drawPath(path)
            self._draw_special_resistors(painter, scale)
            self._draw_ground_symbols(painter, scale)
        if self.mode in {"c", "rc"}:
            self._draw_capacitors(painter)
        self._draw_nodes(painter, scale)
        if self.mode in {"c", "rc"}:
            self._draw_ground_capacitance(painter, scale)
        if self.labels:
            self._draw_labels(painter)

    def _draw_special_resistors(self, painter: QPainter, scale: float) -> None:
        unit = 1.0 / scale
        for path, color, style in (
            (self.zero_path, "#6b7280", Qt.SolidLine),
            (self.warning_path, "#b42318", Qt.DashLine),
        ):
            pen = QPen(QColor(color), 1.5, style)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawPath(path)
        painter.setBrush(Qt.NoBrush)
        for point, color in self.rings:
            pen = QPen(QColor(color), 1.5)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawEllipse(point, 5 * unit, 5 * unit)
        pen = QPen(QColor("#6b7280"), 1.2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for point in self.diamonds:
            r = 4 * unit
            painter.drawPolygon(
                QPolygonF(
                    (
                        QPointF(point.x(), point.y() - r),
                        QPointF(point.x() + r, point.y()),
                        QPointF(point.x(), point.y() + r),
                        QPointF(point.x() - r, point.y()),
                    )
                )
            )

    def _draw_nodes(self, painter: QPainter, scale: float) -> None:
        unit = 1.0 / scale
        local_pen = QPen(QColor("#ffffff"), 0.8)
        local_pen.setCosmetic(True)
        external_pen = QPen(QColor("#6b7280"), 1.2)
        external_pen.setCosmetic(True)
        painter.setBrush(QColor("#374151"))
        for node, point in self.node_hits:
            if not self.node_is_visible(node):
                continue
            if node.external:
                painter.setPen(external_pen)
                painter.setBrush(Qt.NoBrush)
                radius = 3.0 * unit
                painter.drawRect(
                    QRectF(
                        point.x() - radius, point.y() - radius, 2 * radius, 2 * radius
                    )
                )
            else:
                painter.setPen(local_pen)
                painter.setBrush(QColor("#374151"))
                painter.drawEllipse(point, 2.2 * unit, 2.2 * unit)

    def _draw_ground_symbols(self, painter: QPainter, scale: float) -> None:
        unit = 1.0 / scale
        pen = QPen(QColor("#374151"), 1.2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for _node, point in self.ground_hits:
            for offset, half_width in ((2.0, 5.0), (4.5, 3.5), (7.0, 1.5)):
                y = point.y() + offset * unit
                painter.drawLine(
                    QPointF(point.x() - half_width * unit, y),
                    QPointF(point.x() + half_width * unit, y),
                )

    def _draw_capacitors(self, painter: QPainter) -> None:
        for index, path in enumerate(self.c_paths):
            pen = QPen(QColor(_COLORS[index]), 1.5, Qt.DashLine)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawPath(path)
        for path, color, style in (
            (self.c_zero_path, "#6b7280", Qt.DotLine),
            (self.c_warning_path, "#b42318", Qt.DashDotLine),
        ):
            pen = QPen(QColor(color), 1.5, style)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawPath(path)

    def _draw_ground_capacitance(self, painter: QPainter, scale: float) -> None:
        unit = 1.0 / scale
        for node in self.ground_nodes:
            value = node.ground_capacitance
            warning = value < 0 or not math.isfinite(value)
            index = self._bin(value, self._ground_range)
            pen = QPen(QColor("#b42318" if warning else "#ffffff"), 1.4)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(
                Qt.NoBrush if warning else QColor(_COLORS[index])
            )
            painter.drawEllipse(self.points[node.id], 5 * unit, 5 * unit)

    def _draw_labels(self, painter: QPainter) -> None:
        transform = painter.worldTransform()
        painter.save()
        painter.resetTransform()
        painter.setPen(QColor("#20252b"))
        visible = [node for node in self.drawable_nodes if self.node_is_visible(node)]
        if len(visible) > 200:
            visible = [
                node
                for node in visible
                if node.external or node.kind in {"port", "instance_pin"}
            ][:200]
        for node in visible:
            point = transform.map(self.points[node.id])
            painter.drawText(point + QPointF(5, -5), format_node_label(node))
        painter.restore()


class RcNetworkCanvas(QGraphicsView):
    """A zoomable R-only view containing exactly one batched network item."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#ffffff"))
        self.setRenderHint(QPainter.Antialiasing, True)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setMouseTracking(True)
        self.network = self.layout = self.item = None
        self._middle_pos: QPoint | None = None

    def set_network(self, network: Any, layout: Any) -> None:
        self.clear_network()
        self.network, self.layout = network, layout
        self.item = _NetworkItem(network, layout)
        self.scene().addItem(self.item)
        self.scene().setSceneRect(self.item.boundingRect())
        QTimer.singleShot(0, self.fit_network)

    def clear_network(self) -> None:
        self.scene().clear()
        self.network = self.layout = self.item = None

    def set_mode(self, mode: str) -> None:
        if mode not in {"r", "c", "rc"}:
            raise ValueError(f"unsupported RC display mode: {mode}")
        if self.item:
            self.item.mode = mode
            self.item.update()

    def set_labels(self, visible: bool) -> None:
        if self.item:
            self.item.labels = visible
            self.item.update()

    def fit_network(self) -> None:
        if not self.item:
            return
        rect = QRectF(
            self.item.fit_rect if self.item.mode == "r" else self.item.all_fit_rect
        )
        span = max(rect.width(), rect.height(), 1.0)
        rect.adjust(-span * 0.04, -span * 0.04, span * 0.04, span * 0.04)
        if rect.width() < span * 0.02:
            rect.adjust(-span * 0.01, 0, span * 0.01, 0)
        if rect.height() < span * 0.02:
            rect.adjust(0, -span * 0.01, 0, span * 0.01)
        self.fitInView(rect, Qt.KeepAspectRatio)

    def zoom_by(self, factor: float) -> None:
        current = abs(self.transform().m11())
        factor = (
            min(factor, 1e4 / current) if factor > 1 else max(factor, 1e-4 / current)
        )
        self.scale(factor, factor)

    def wheelEvent(self, event: QWheelEvent) -> None:
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.zoom_by(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        event.accept()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MiddleButton:
            self._middle_pos = event.pos()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._middle_pos is not None:
            point = event.pos()
            delta = point - self._middle_pos
            self._middle_pos = point
            self.horizontalScrollBar().setValue(
                self.horizontalScrollBar().value() - delta.x()
            )
            self.verticalScrollBar().setValue(
                self.verticalScrollBar().value() - delta.y()
            )
            event.accept()
            return
        text = self.tooltip_at(self.mapToScene(event.pos()))
        QToolTip.showText(
            event.globalPos(), text, self
        ) if text else QToolTip.hideText()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            event.button() == Qt.MiddleButton
            and self._middle_pos is not None
        ):
            self._middle_pos = None
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def tooltip_at(self, point: QPointF, tolerance_pixels: float = 7.0) -> str | None:
        if not self.item:
            return None
        tolerance = tolerance_pixels / max(abs(self.transform().m11()), 1e-12)
        candidates = [
            (math.hypot(point.x() - pos.x(), point.y() - pos.y()), "node", node)
            for node, pos in (*self.item.node_hits, *self.item.ground_hits)
            if self.item.node_is_visible(node)
        ]
        allowed = (
            {"resistor"} if self.item.mode == "r"
            else {"capacitor"} if self.item.mode == "c"
            else {"resistor", "capacitor"}
        )
        candidates += [
            (_distance(point, first, second), kind, part)
            for kind, part, first, second in self.item.hits
            if kind in allowed
        ]
        if not candidates:
            return None
        distance, kind, part = min(candidates, key=lambda candidate: candidate[0])
        return (
            format_tooltip(
                kind, part, self.item.nodes,
                include_capacitance=self.item.mode != "r",
            )
            if distance <= tolerance
            else None
        )

    def legend_range(
        self, mode: str | None = None,
    ) -> tuple[float | None, float | None, str]:
        if not self.item:
            return None, None, ""
        mode = mode or self.item.mode
        limits = self.item._r_range if mode == "r" else self.item._c_range
        unit = "ohm" if mode == "r" else "F"
        return (
            (*limits, unit)
            if limits
            else (None, None, unit)
        )


__all__ = ["RcNetworkCanvas", "format_node_label", "format_tooltip"]
