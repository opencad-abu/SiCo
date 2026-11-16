"""Export actual saved OA schematics for separate visual review, never render a mockup."""

import hashlib
import json
import struct


def export_schematic(bridge, directory, target, name):
    """Read-only native export; producing an image is not a visual pass verdict."""
    path = directory / (name + ".png")
    identity = " ".join(json.dumps(target[k]) for k in ("library", "cell", "view"))
    bridge.skill(
        "tplVisualWindow=deOpenCellView(" + identity + ' "schematic" nil "r")'
    )
    assert bridge.skill("hiExportImage(?window tplVisualWindow ?fileName "
                        + json.dumps(str(path))
                        + " ?exportRegion 'entireDesign ?width 2400 ?height 1600)") == "t"
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) > 5000
    width, height = struct.unpack(">II", raw[16:24])
    assert width >= 1200 and height >= 800
    return {"target": target, "path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
            "width": width, "height": height, "source": "Virtuoso.hiExportImage",
            "review_status": "pending_visual_review"}
