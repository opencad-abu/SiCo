"""Display rows for the @ object picker."""

from __future__ import annotations

from sico.core.cad_ref import object_ref

KIND_LABELS = {
    "instance": "器件",
    "net": "网络",
    "terminal": "引脚",
    "shape": "图形",
    "cellview": "窗口",
}


def valid_window_ref(value):
    return value if isinstance(value, str) and value.startswith("wctx_") else None


def window_label(target, window=None, title=None):
    if not isinstance(target, dict):
        return str(window or "")
    label = "/".join((target["library"], target["cell"], target["view_name"]))
    if window:
        label += " · " + str(window)
    if isinstance(title, str) and title:
        clean = " ".join(title.split())
        if clean:
            label += " · " + clean[:80]
    return label[:256]


def window_rows(target, window_ref, label, other_windows, include_current=True):
    if not isinstance(target, dict):
        rows = []
    else:
        label = label or window_label(target)
        name = "/".join((target["library"], target["cell"], target["view_name"]))
        rows = [{
            "label": label,
            "detail": "当前窗口 · cellview",
        "window_ref": valid_window_ref(window_ref),
            "window_label": label,
            "ref": object_ref(target, {
                "kind": "cellview", "name": name, "hierarchy": [],
            }, require_master=False),
        }] if include_current else []
    for window in other_windows:
        target = window.get("target")
        if not isinstance(target, dict) or window.get("window_ref") == window_ref:
            continue
        other_label = window_label(target, window.get("window"), window.get("title"))
        rows.append({
            "label": other_label,
            "detail": "其他窗口 · cellview",
            "window_ref": valid_window_ref(window["window_ref"]),
            "window_label": other_label,
            "switch_window": window,
        })
    return rows


def object_rows(target, items, source, window_ref=None, label=None):
    rows = []
    for item in items:
        detail = (label or source) + " · " + KIND_LABELS.get(
            item["kind"], item["kind"])
        if item.get("master"):
            master = item["master"]
            detail += " · " + "/".join(
                (master["library"], master["cell"], master["view_name"]))
        rows.append({
            "label": item["name"],
            "detail": detail,
            "ref": object_ref(target, item),
            "window_ref": valid_window_ref(window_ref),
            "window_label": label,
        })
    return rows


__all__ = ["KIND_LABELS", "object_rows", "window_label", "window_rows"]
