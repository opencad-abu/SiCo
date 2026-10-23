"""Classify captured symbol labels and preserve only literal, non-executed text."""

from __future__ import annotations

import json
import math
import re

from .template_schema import TemplateUnavailable, canonical

MAX_LABELS = 64
MAX_TEXT = 256
PARAM = re.compile(r"cdsParam\(([1-9][0-9]{0,2})\)\Z")
FIXED = {"cdsName()", "[@partName]", "[@cellName]", "[@instanceName]"}
JUSTIFY = {
    "centerLeft",
    "centerRight",
    "centerCenter",
    "lowerLeft",
    "lowerRight",
    "lowerCenter",
    "upperLeft",
    "upperRight",
    "upperCenter",
}
ORIENT = {"R0", "R90", "R180", "R270"}


def _scaled(value, scale, unit):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise TemplateUnavailable("preserved symbol label has a non-finite coordinate")
    result = round(value * scale / unit, 8)
    if abs(result) > 256:
        raise TemplateUnavailable("preserved symbol label coordinate is out of range")
    return result


def _point(value, scale, unit):
    if not isinstance(value, list) or len(value) != 2:
        raise TemplateUnavailable("preserved symbol label requires a two-dimensional position")
    return [_scaled(value[i], scale, unit) for i in (0, 1)]


def _box(value, scale, unit):
    if not isinstance(value, list) or len(value) != 2:
        raise TemplateUnavailable("preserved symbol label requires a bounding box")
    result = [_point(value[0], scale, unit), _point(value[1], scale, unit)]
    if any(result[0][i] > result[1][i] for i in (0, 1)):
        raise TemplateUnavailable("preserved symbol label has an inverted bounding box")
    return result


def _text(figure):
    value = figure.get("text")
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise TemplateUnavailable("preserved symbol label text is missing or too long")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise TemplateUnavailable("preserved symbol label text contains control characters")
    return value


def _kind(figure, text, terminals):
    label_type = figure.get("labelType")
    if label_type == "normalLabel" and text in terminals:
        return "pin_label"
    if text in FIXED:
        return "fixed_annotation"
    if label_type == "ILLabel" and text in {
        "cdsTerm(" + json.dumps(name) + ")" for name in terminals
    }:
        return "cds_term"
    if label_type == "ILLabel" and text in {"cdsTerm(" + name + ")" for name in terminals}:
        return "cds_term"
    if PARAM.fullmatch(text):
        return "cds_param"
    if label_type == "ILLabel" or label_type not in {"NLPLabel", "normalLabel"}:
        return "dynamic"
    return "plain_text"


def _annotation(figure, text, kind, scale, unit):
    if kind == "cds_param":
        render_type = "NLPLabel"
        rendered = text
    else:
        render_type = figure.get("labelType")
        rendered = text
    if render_type not in {"NLPLabel", "normalLabel"}:
        raise TemplateUnavailable("preserved symbol label requires a literal label type")
    justify = figure.get("justify")
    orient = figure.get("orient")
    font = figure.get("font")
    height = figure.get("height")
    if justify not in JUSTIFY or orient not in ORIENT:
        raise TemplateUnavailable("preserved symbol label has unsupported alignment")
    if not isinstance(font, str) or not font or len(font) > 32:
        raise TemplateUnavailable("preserved symbol label has an invalid font")
    if type(height) not in (int, float) or not math.isfinite(height) or height <= 0:
        raise TemplateUnavailable("preserved symbol label has an invalid height")
    scaled_height = _scaled(height, scale, unit)
    if not 1e-6 <= scaled_height <= 4:
        raise TemplateUnavailable("preserved symbol label height is out of range")
    lpp = figure.get("lpp")
    return {
        "kind": "cds_param_placeholder" if kind == "cds_param" else "plain_text",
        "text": rendered,
        "source_text": text,
        "xy": _point(figure.get("xy"), scale, unit),
        "bbox": _box(figure.get("bBox"), scale, unit),
        "justify": justify,
        "orient": orient,
        "font": font,
        "height": scaled_height,
        "label_type": render_type,
        "source_label_type": figure.get("labelType"),
        "source_lpp": list(lpp) if isinstance(lpp, list) else [],
        "safe_renderer": (
            "cds_param_placeholder_not_evaluated" if kind == "cds_param" else "literal_label"
        ),
    }


def label_overlaps(annotations):
    """Report pairwise overlaps among preserved label bounding boxes."""
    result = []
    for index, left in enumerate(annotations):
        left_box = left["bbox"]
        for right in annotations[index + 1 :]:
            right_box = right["bbox"]
            if (
                left_box[0][0] < right_box[1][0]
                and left_box[1][0] > right_box[0][0]
                and left_box[0][1] < right_box[1][1]
                and left_box[1][1] > right_box[0][1]
            ):
                overlap_width = min(left_box[1][0], right_box[1][0]) - max(
                    left_box[0][0], right_box[0][0]
                )
                overlap_height = min(left_box[1][1], right_box[1][1]) - max(
                    left_box[0][1], right_box[0][1]
                )
                left_area = (left_box[1][0] - left_box[0][0]) * (left_box[1][1] - left_box[0][1])
                right_area = (right_box[1][0] - right_box[0][0]) * (
                    right_box[1][1] - right_box[0][1]
                )
                ratio = (
                    overlap_width * overlap_height / min(left_area, right_area)
                    if min(left_area, right_area) > 0
                    else 0
                )
                if ratio < 0.1:
                    continue
                result.append(
                    {
                        "left": left["text"],
                        "right": right["text"],
                        "left_bbox": left_box,
                        "right_bbox": right_box,
                        "overlap_ratio": round(ratio, 6),
                    }
                )
    return result


def inventory_labels(asset, terminals):
    """Describe every captured label without scaling or executing it."""
    counts, labels = {}, []
    for shape in asset.get("shapes", []):
        figure = shape.get("figure") or {}
        if figure.get("objType") != "label":
            continue
        try:
            text = _text(figure)
            kind = _kind(figure, text, terminals)
        except TemplateUnavailable as exc:
            text, kind = str(figure.get("text", "")), "invalid"
            labels.append({"text": text, "kind": kind, "error": str(exc)})
        else:
            labels.append(
                {
                    "text": text,
                    "kind": kind,
                    "label_type": figure.get("labelType"),
                    "xy": figure.get("xy"),
                    "bbox": figure.get("bBox"),
                }
            )
        counts[kind] = counts.get(kind, 0) + 1
    return {"counts": counts, "labels": labels}


def extract_labels(asset, terminals, *, scale=1, unit=1, policy="strict"):
    """Return preserved annotations and labels unresolved by the selected policy."""
    annotations, unresolved, dynamic, seen = [], [], [], set()
    for shape in asset.get("shapes", []):
        figure = shape.get("figure") or {}
        if figure.get("objType") != "label":
            continue
        try:
            text = _text(figure)
            kind = _kind(figure, text, terminals)
        except TemplateUnavailable as exc:
            dynamic.append(str(exc))
            continue
        if kind in {"pin_label", "fixed_annotation", "cds_term"}:
            continue
        if kind == "dynamic":
            dynamic.append(text)
            continue
        try:
            annotation = _annotation(figure, text, kind, scale, unit)
        except TemplateUnavailable as exc:
            dynamic.append(str(exc))
            continue
        key = canonical(annotation)
        if key in seen:
            continue
        seen.add(key)
        if policy == "preserve":
            annotations.append(annotation)
        else:
            unresolved.append(text)
    annotations.sort(key=lambda row: (row["text"], row["xy"], row["kind"]))
    if len(annotations) + len(unresolved) > MAX_LABELS:
        raise TemplateUnavailable("symbol label budget exceeded")
    return {
        "annotations": annotations,
        "unresolved": sorted(set(unresolved)),
        "dynamic": sorted(set(dynamic)),
        "overlaps": label_overlaps(annotations),
    }
