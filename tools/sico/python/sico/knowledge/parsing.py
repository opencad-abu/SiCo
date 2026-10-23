"""Bounded text/Markdown/PDF extraction with line or page locators."""

from pathlib import Path

from .contracts import CHUNK_BYTES, KnowledgeError, clipped


def chunks(text, *, page=None):
    result, lines, start, end, size = [], [], 1, 1, 0
    for number, line in enumerate(text.splitlines(), 1):
        pieces = []
        while len(line.encode()) > CHUNK_BYTES:
            piece = clipped(line, CHUNK_BYTES)
            pieces.append(piece)
            line = line[len(piece):]
        pieces.append(line)
        for piece in pieces:
            if lines and size + len(piece.encode()) + 1 > CHUNK_BYTES:
                result.append({"text": "\n".join(lines), "start": start, "end": end,
                               "page": page})
                lines, size, start = [], 0, number
            if not lines:
                start = number
            lines.append(piece)
            end = number
            size += len(piece.encode()) + 1
    if lines:
        result.append({"text": "\n".join(lines), "start": start,
                       "end": end, "page": page})
    return [row for row in result if row["text"].strip()]


def extract(name, data, cancelled, deadline):
    if Path(name).suffix.lower() != ".pdf":
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise KnowledgeError("encoding", "文本资料需要 UTF-8 编码") from None
        if "\x00" in text:
            raise KnowledgeError("encoding", "资料包含二进制内容")
        return chunks(text), []
    from .pdf_text import pages

    result, issues = [], []
    for page, text in enumerate(pages(data, cancelled, deadline), 1):
        if not text.strip():
            issues.append({"document": name, "page": page, "status": "ocr_required"})
        result.extend(chunks(text, page=page))
    return result, issues
