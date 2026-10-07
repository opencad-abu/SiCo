"""Deterministic SVG reference previews and a conservative symbol layout recipe."""

from __future__ import annotations

import hashlib
import html
import json
import math
import unicodedata
from pathlib import Path

from .template_coords import instance_position, is_integer_space
from .template_schema import PIN_POLICY, TemplateUnavailable, canonical


def symbol_plan(ports, title):
    """Geometry only. Interface and electrical order are never inferred or changed."""
    if len(ports) > 512:
        raise TemplateUnavailable(
            "symbol proposal exceeds 512 ports; query source geometry instead"
        )
    grouped = {k: [] for k in PIN_POLICY}
    unknown = []
    for p in ports:
        direction = p.get("direction")
        if direction in grouped:
            grouped[direction].append(p)
        else:
            unknown.append(p["name"])
    # Input order is stable from the capture. Explicit role grouping may be added by callers.
    left = grouped["inputOutput"] + grouped["input"]
    right = grouped["output"]

    def text_width(text):
        return sum(2 if unicodedata.east_asian_width(c) in {"W", "F"} else 1 for c in text) * 9

    left_width = max((text_width(p["name"]) for p in left), default=0)
    right_width = max((text_width(p["name"]) for p in right), default=0)
    width = max(240, left_width + right_width + 100, text_width(title) + 60)
    width = int((width + 4) // 5 * 5)
    separation = 30 if grouped["inputOutput"] and grouped["input"] else 0
    height = max(120, (max(len(left), len(right)) + 1) * 30 + separation)
    pins = []
    for group, x, step, start in (
        (grouped["inputOutput"], 0, 30, 30),
        (grouped["input"], 0, 30, height - len(grouped["input"]) * 30),
        (right, width, 30, (height - (len(right) - 1) * 30) / 2),
    ):
        for index, port in enumerate(group):
            pins.append(
                {**port, "x": x, "y": start + index * step, "side": "left" if x == 0 else "right"}
            )
    anchors = {(p["x"], p["y"]) for p in pins}
    return {
        "style": "rectangular_module",
        "title": title,
        "width": width,
        "height": height,
        "pins": pins,
        "font_height": 14,
        "pin_pitch": 30,
        "stub": 25,
        "grid": 5,
        "pin_policy": PIN_POLICY,
        "unknown_direction_ports": unknown,
        "checks": {
            "unique_anchors": len(anchors) == len(pins),
            "all_ports_placed": not unknown,
            "unique_port_names": len({p["name"] for p in ports}) == len(ports),
            "label_spacing": all(
                abs(a["y"] - b["y"]) >= 30
                for i, a in enumerate(pins)
                for b in pins[i + 1 :]
                if a["side"] == b["side"]
            ),
            "text_width_basis": "conservative_estimate",
            "on_grid": all(p["x"] % 5 == 0 and p["y"] % 5 == 0 for p in pins),
        },
        "qualification": "geometry_preview_only; actual_font_and_Cadence_rendering_not_verified",
    }


def symbol_svg(plan):
    width, height, pad = plan["width"], plan["height"], 60
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width + pad * 2}" '
        f'height="{height + 140}" viewBox="{-pad} -65 {width + pad * 2} {height + 140}">',
        '<rect x="-60" y="-65" width="100%" height="100%" fill="#fff"/>',
        f'<text x="{width / 2}" y="-35" text-anchor="middle" font-family="sans-serif" '
        f'font-size="17" fill="#152c42">{html.escape(plan["title"])}</text>',
        f'<rect x="0" y="0" width="{width}" height="{height}" rx="2" '
        'fill="#f4f8fc" stroke="#23435e" stroke-width="2"/>',
    ]
    for p in plan["pins"]:
        x, y = p["x"], p["y"]
        outside = x - 25 if p["side"] == "left" else x + 25
        text_x = x + 12 if p["side"] == "left" else x - 12
        anchor = "start" if p["side"] == "left" else "end"
        out.extend(
            [
                f'<line x1="{x}" y1="{y}" x2="{outside}" y2="{y}" '
                'stroke="#23435e" stroke-width="2"/>',
                f'<circle cx="{outside}" cy="{y}" r="3" fill="#327ea6"/>',
                f'<text x="{text_x}" y="{y + 5}" text-anchor="{anchor}" '
                f'font-family="monospace" font-size="14" fill="#152c42">'
                f"{html.escape(p['name'])}</text>",
            ]
        )
    out.append(
        f'<text x="{width / 2}" y="{height + 40}" text-anchor="middle" '
        'font-family="monospace" font-size="12" fill="#637586">'
        "instance / parameters</text></svg>"
    )
    return "\n".join(out)


def _figure(fig, color="#365a72", stroke_width=0.012):
    typ, box, points = fig.get("objType"), fig.get("bBox"), fig.get("points")
    stroke = f'stroke="{color}" stroke-width="{stroke_width}" fill="none"'
    if typ == "arc" and fig.get("ellipseBBox"):
        outer = fig["ellipseBBox"]
        a, b = fig.get("startAngle"), fig.get("stopAngle")
        if a is None or b is None:
            return ""
        rx, ry = [(outer[1][i] - outer[0][i]) / 2 for i in (0, 1)]
        cx, cy = [(outer[1][i] + outer[0][i]) / 2 for i in (0, 1)]
        start = (cx + rx * math.cos(a), -(cy + ry * math.sin(a)))
        end = (cx + rx * math.cos(b), -(cy + ry * math.sin(b)))
        large = int((b - a) % (2 * math.pi) > math.pi + 1e-9)
        return (
            f'<path d="M {start[0]} {start[1]} A {rx} {ry} 0 {large} 0 '
            f'{end[0]} {end[1]}" {stroke}/>'
        )
    if typ == "pathSeg" and fig.get("beginPt") and fig.get("endPt"):
        points = [fig["beginPt"], fig["endPt"]]
    if typ in {"line", "path", "pathSeg", "polygon"} and points:
        tag = "polygon" if typ == "polygon" else "polyline"
        return f'<{tag} points="' + " ".join(f"{p[0]},{-p[1]}" for p in points) + f'" {stroke}/>'
    if typ in {"rect", "rectangle"} and box:
        return (
            f'<rect x="{box[0][0]}" y="{-box[1][1]}" width="{box[1][0] - box[0][0]}" '
            f'height="{box[1][1] - box[0][1]}" {stroke}/>'
        )
    if typ in {"ellipse", "circle"} and box:
        return (
            f'<ellipse cx="{(box[0][0] + box[1][0]) / 2}" '
            f'cy="{-(box[0][1] + box[1][1]) / 2}" rx="{(box[1][0] - box[0][0]) / 2}" '
            f'ry="{(box[1][1] - box[0][1]) / 2}" {stroke}/>'
        )
    if typ == "label" and fig.get("xy") and fig.get("text"):
        x, y = fig["xy"]
        height = fig.get("height") or 0.08
        justify = fig.get("justify") or "lowerLeft"
        anchor = (
            "end"
            if justify.endswith("Right")
            else ("middle" if justify.endswith("Center") else "start")
        )
        baseline = (
            "middle"
            if justify.startswith("center")
            else ("hanging" if justify.startswith("upper") else "auto")
        )
        angle = {"R90": -90, "R180": -180, "R270": -270}.get(fig.get("orient"), 0)
        return (
            f'<text x="{x}" y="{-y}" text-anchor="{anchor}" dominant-baseline="{baseline}" '
            f'transform="rotate({angle} {x} {-y})" font-family="monospace" font-size="{height}" '
            f'fill="{color}">{html.escape(fig["text"])}</text>'
        )
    return ""


def source_svg(asset, asset_type):
    source = asset.get("source", asset)
    if is_integer_space(asset):
        box = asset.get("bbox") or [[0, 0], [1000, 1000]]
    else:
        box = source.get("bbox") or asset.get("bbox") or [[0, 0], [10, 10]]
    w, h = max(1, box[1][0] - box[0][0]), max(1, box[1][1] - box[0][1])
    margin = max(w, h) * 0.08
    stroke = max(w, h) / 1000.0
    viewbox = f"{box[0][0] - margin} {-box[1][1] - margin} {w + 2 * margin} {h + 2 * margin}"
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="700" '
        f'style="background:white" viewBox="{viewbox}">',
        f'<rect x="{box[0][0] - margin}" y="{-box[1][1] - margin}" '
        f'width="{w + 2 * margin}" height="{h + 2 * margin}" fill="white"/>',
    ]
    unsupported = CounterTypes()
    seen = set()
    dynamic = 0
    for shape in asset.get("shapes", []) + asset.get("pins", []):
        figure = shape.get("figure") or {}
        key = canonical(figure)
        if key in seen:
            continue
        seen.add(key)
        if figure.get("labelType") in {"ILLabel", "NLPLabel"}:
            dynamic += 1
        svg = _figure(figure, stroke_width=stroke)
        if figure.get("lpp") == ["instance", "drawing"]:
            svg = f'<g opacity="0.4" stroke-dasharray="{stroke * 2} {stroke * 1.6}">' + svg + "</g>"
        if svg:
            parts.append(svg)
        else:
            unsupported.add(figure.get("objType", "missing_figure"))
    for inst in asset.get("instances", []):
        bbox = inst.get("bbox")
        xy = instance_position(inst)
        if bbox:
            parts.append(_figure({"objType": "rect", "bBox": bbox}, "#d48b35", stroke_width=stroke))
        if xy:
            parts.append(
                _figure(
                    {
                        "objType": "label",
                        "xy": xy,
                        "height": max(w, h) / 100,
                        "text": inst.get("source_name", inst.get("name", "?")),
                    },
                    "#a76515",
                    stroke_width=stroke,
                )
            )
    parts.append("</svg>")
    return "\n".join(parts), {
        "unsupported_figures": unsupported.values,
        "dynamic_labels_rendered_as_literal": dynamic,
        "instance_rendering": "bounding_boxes_and_names",
        "asset": asset_type,
        "scope": "reference_geometry; not_Cadence_rendering",
    }


class CounterTypes:
    def __init__(self):
        self.values = {}

    def add(self, name):
        self.values[name] = self.values.get(name, 0) + 1


def _write_once(path, value):
    raw = value.encode()
    from .template_build import publish_bytes

    publish_bytes(path, raw)
    return {"path": str(path), "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def render_preview(record, root):
    directory = Path(root) / (record["template_ref"] + "-preview4")
    directory.mkdir(parents=True, exist_ok=True)
    artifacts, sections, reports = [], [], {}
    for name, asset in record["assets"].items():
        svg, report = source_svg(asset, name)
        artifacts.append(_write_once(directory / (name + ".svg"), svg))
        reports[name] = report
        sections.append(
            f"<h2>{html.escape(name)} — source geometry</h2>"
            f'<img src="{name}.svg" alt="{name} source reference">'
        )
    ports = record.get("topology", {}).get("ports")
    if ports is None and "symbol" in record["assets"]:
        ports = record["assets"]["symbol"]["ports"]
    if ports is not None:
        plan = symbol_plan(ports, record["source"]["cell"])
        artifacts.append(_write_once(directory / "symbol-plan.json", canonical(plan) + "\n"))
        artifacts.append(_write_once(directory / "symbol-proposed.svg", symbol_svg(plan)))
        reports["symbol_proposed"] = plan["checks"]
        sections.append(
            '<h2>Symbol — port policy proposal</h2><img src="symbol-proposed.svg" '
            'alt="Generated symbol placement proposal">'
        )
    page = (
        '<!doctype html><meta charset="utf-8"><title>Circuit template preview</title>'
        "<style>body{font:16px sans-serif;max-width:1100px;margin:32px auto;color:#18344b}"
        "img{max-width:100%;background:white;border:1px solid #d8e2eb}"
        "pre{white-space:pre-wrap}h2{margin-top:36px}</style>"
    )
    page += "<h1>" + html.escape(record["source"]["lib"] + "/" + record["source"]["cell"]) + "</h1>"
    page += (
        "<p>Reference geometry and a rectangular symbol proposal. Instance bodies are shown "
        "as bounding boxes; unsupported figures and incomplete bindings are listed below.</p>"
    )
    page += (
        "\n".join(sections)
        + "<h2>Coverage</h2><pre>"
        + html.escape(json.dumps(reports, ensure_ascii=False, indent=2))
        + "</pre>"
    )
    artifacts.append(_write_once(directory / "index.html", page))
    return {
        "ok": True,
        "template_ref": record["template_ref"],
        "artifacts": artifacts,
        "reports": reports,
        "preview": str(directory / "index.html"),
    }
