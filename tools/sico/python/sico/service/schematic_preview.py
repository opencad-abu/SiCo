"""Read report SVGs off the UI thread; never send their contents to the model."""

from pathlib import Path

from cadai.circuit_preview import SCHEMA, read_svg_artifact


def preview_images(value, reader):
    """Return one verified image from a creation or durable operation report."""
    pending = [value]
    for _ in range(32):
        if not pending:
            break
        row = pending.pop(0)
        if not isinstance(row, dict):
            continue
        preview = row.get("svg_preview")
        if isinstance(preview, dict) and preview.get("schema") == SCHEMA:
            if preview.get("status") != "ready":
                return [], "原理图预览未生成；电路创建结果保留。"
            try:
                workspace = Path(reader.root).parents[2]  # workspace/.sico/ai/agent
                return [read_svg_artifact(preview["artifact"], workspace)], ""
            except (OSError, ValueError, KeyError, TypeError):
                return [], "原理图预览文件暂不可读或校验失败；电路创建结果保留。"
        pending.extend(row[k] for k in ("data", "last_response", "receipt", "created") if k in row)
    return [], ""
