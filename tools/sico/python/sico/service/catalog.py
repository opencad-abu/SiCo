"""Background history discovery with copy-on-publish navigation snapshots."""

from __future__ import annotations

import threading
import time
from concurrent.futures import Future

from ..storage.history import CatalogIndex
from .published import freeze


class CatalogService:
    MAX_LABEL_ROWS = 2048

    def __init__(self, root, controllers, labels=None):
        self._index = CatalogIndex(root)
        self._controllers = controllers
        self._labels = labels
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._requests = []
        self._snapshot = freeze({
            "version": 0, "rows": (), "retained": {}, "availability": {}, "error": "",
            "complete": False, "labels": self._empty_labels(), "label_rows": (),
        })
        self._thread = threading.Thread(target=self._run, name="copilot-catalog", daemon=True)
        self._thread.start()

    def request(self):
        future = Future()
        with self._lock:
            if self._stop.is_set():
                future.set_exception(RuntimeError("Catalog is closed"))
                return future
            if len(self._requests) >= 64:
                future.set_exception(ValueError("Catalog request queue is full"))
                return future
            self._requests.append(future)
            self._wake.set()
        return future

    def snapshot(self, since_version=None):
        with self._lock:
            value = self._snapshot
        return None if value["version"] == since_version else value

    def publish_labels(self, labels):
        """Publish a label mutation immediately without rescanning session journals."""

        label_state = self._label_value(lambda: labels)
        with self._lock:
            previous = self._snapshot
        value = dict(previous)
        value["labels"] = label_state
        referenced = set(label_state["loose_order"])
        for label in label_state["labels"]:
            referenced.update(label.get("sessions", ()))
        known = {}
        for row in (*previous["rows"], *previous["label_rows"],
                    *previous["retained"].values()):
            if isinstance(row, dict) and row.get("id"):
                known[row["id"]] = row
        base_ids = {row["id"] for row in previous["rows"]}
        extra = [
            row for key, row in known.items()
            if key in referenced and key not in base_ids
            and not row.get("unavailable")
        ]
        extra.sort(key=lambda row: row.get("modified", 0), reverse=True)
        value["label_rows"] = tuple(extra[:self.MAX_LABEL_ROWS])
        self._store(value)
        return self.snapshot()["version"]

    def _run(self):
        while not self._stop.is_set():
            with self._lock:
                self._wake.clear()
                requests, self._requests = self._requests, []
            try:
                controllers = self._controllers()
                # A first-ever scan walks every journal; publish what is ready
                # (newest sessions first) instead of freezing the pane.
                last_publish = [0.0]

                def progress(partial, controllers=controllers):
                    now = time.monotonic()
                    if now - last_publish[0] < 0.25:
                        return
                    last_publish[0] = now
                    self._publish(partial, controllers)

                available = self._index.scan(progress)
                value = self._value(available, controllers, labels=self._labels)
            except Exception as exc:
                value = {"rows": (), "retained": {}, "availability": {}, "error": str(exc),
                         "complete": False, "labels": self._empty_labels(),
                         "label_rows": ()}
            version = self._store(value)
            for future in requests:
                if not future.cancelled():
                    future.set_result(version)
            self._wake.wait(3)
        with self._lock:
            requests, self._requests = self._requests, []
        for future in requests:
            if not future.cancelled():
                future.set_exception(RuntimeError("Catalog is closed"))

    def close(self):
        self._stop.set()
        self._wake.set()

    @staticmethod
    def _value(available, controllers, *, complete=True, labels=None):
        available = dict(available)
        for controller in controllers:
            row = available.get(controller.session_id)
            if row and row.get("damaged"):
                available[controller.session_id] = dict(row, damaged=False, unavailable="记录不可用")
        rows = sorted(
            (row for row in available.values() if not row.get("unavailable") or row.get("damaged")),
            key=lambda row: row["modified"], reverse=True,
        )[:200]
        label_state = CatalogService._label_value(labels)
        base_ids = {row["id"] for row in rows}
        referenced = set(label_state["loose_order"])
        for label in label_state["labels"]:
            referenced.update(label.get("sessions", ()))
        extra = sorted(
            (
                row for key, row in available.items()
                if key in referenced and key not in base_ids
                and not row.get("unavailable")
            ),
            key=lambda row: row.get("modified", 0), reverse=True,
        )[:CatalogService.MAX_LABEL_ROWS]
        retained = {}
        for controller in controllers:
            key = controller.session_id
            if not complete and key not in available:
                continue  # Not scanned yet is not evidence of unavailability.
            # Availability describes the record. Runtime closure is published by
            # the session owner and must not make a readable archive unavailable.
            row = dict(available.get(key, {"id": key, "unavailable": "记录不可用"}))
            retained[key] = row
        return {
            "rows": tuple(rows), "retained": retained, "error": "", "complete": complete,
            "availability": {key: row.get("unavailable", "")
                             for key, row in available.items()},
            "labels": label_state,
            "label_rows": tuple(extra),
        }

    def _publish(self, available, controllers):
        # Partial scans contain only the visited prefix. Preserve known rows
        # until the final scan can establish deletion/unavailability; otherwise
        # every scan briefly marks a live session missing, then restores it.
        previous = self.snapshot()
        merged = {row["id"]: dict(row) for row in previous["rows"]}
        merged.update({key: dict(row) for key, row in previous["retained"].items()})
        for key, reason in previous["availability"].items():
            if reason and key not in merged:
                merged[key] = {"id": key, "unavailable": reason}
        merged.update(available)
        value = self._value(merged, controllers, complete=False, labels=self._labels)
        value["availability"] = {**previous["availability"], **value["availability"]}
        self._store(value)

    def _store(self, value):
        # The catalog worker is the sole publisher. Comparison and deep freezing
        # must not hold the lock used by Qt's snapshot reference lookup.
        with self._lock:
            previous = self._snapshot
        value = dict(value)
        value["version"] = previous["version"]
        if value == previous:
            return previous["version"]
        value["version"] += 1
        published = freeze(value)
        with self._lock:
            self._snapshot = published
        return published["version"]

    def wait(self, timeout=None):
        self._thread.join(timeout)
        return not self._thread.is_alive()

    @staticmethod
    def _empty_labels():
        return {"revision": None, "labels": (), "loose_order": (), "error": ""}

    @staticmethod
    def _label_value(labels):
        if labels is None:
            return CatalogService._empty_labels()
        try:
            value = labels()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return {**CatalogService._empty_labels(), "error": str(exc)}
        if not isinstance(value, dict):
            return {**CatalogService._empty_labels(), "error": "标签布局不可读"}
        revision = value.get("revision")
        rows = value.get("labels")
        loose = value.get("loose_order")
        if (revision is not None and not isinstance(revision, str)
                or not isinstance(rows, (list, tuple))
                or not isinstance(loose, (list, tuple))):
            return {**CatalogService._empty_labels(), "error": "标签布局不可读"}
        clean = []
        for row in rows:
            if (not isinstance(row, dict)
                    or not isinstance(row.get("id"), str)
                    or not isinstance(row.get("name"), str)
                    or not isinstance(row.get("sessions"), (list, tuple))):
                return {**CatalogService._empty_labels(), "error": "标签布局不可读"}
            clean.append({
                "id": row["id"],
                "name": row["name"],
                "sessions": tuple(row["sessions"]),
            })
        return {
            "revision": revision,
            "labels": tuple(clean),
            "loose_order": tuple(loose),
            "error": str(value.get("error", "")),
        }
