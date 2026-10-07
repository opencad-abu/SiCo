"""Bounded local HTML reader. No browser, JavaScript, network or third-party parser."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

from .socket_server import RequestFailure

MAX_HTML_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 1024 * 1024
HTML_SUFFIXES = {".html", ".htm", ".xhtml"}


def local_page(doc: Path, reference: str, current: Path | None = None) -> tuple[Path, str]:
    try:
        url = urlsplit(reference)
        if url.scheme or url.netloc or url.query:
            raise ValueError("only local HTML references without query strings are supported")
        relative = unquote(url.path)
        if "\x00" in relative or Path(relative).is_absolute():
            raise ValueError("page must be doc-relative")
        parent = current.parent if current is not None else doc
        candidate = (parent / relative) if relative else current
        if candidate is None:
            raise ValueError("page path is required")
        path = candidate.resolve(strict=True)
        path.relative_to(doc)
        if path.suffix.lower() not in HTML_SUFFIXES:
            raise ValueError("page must be HTML")
        return path, unquote(url.fragment)
    except (OSError, ValueError, RuntimeError) as exc:
        raise RequestFailure("invalid_page", str(exc)) from exc


class ManualHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.length = 0
        self.truncated = False
        self.hidden = []
        self.pre = 0
        self.in_title = False
        self.title = ""
        self.anchors = {}
        self.links = []
        self.link_count = 0
        self.active_link = None

    def append(self, text):
        room = MAX_TEXT_CHARS - self.length
        if len(text) > room:
            self.truncated = True
        self.parts.append(text[:room])
        self.length += min(len(text), room)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag in {"script", "style", "template"}:
            self.hidden.append(tag)
        if self.hidden:
            return
        for key in ("id", "name"):
            if values.get(key) and len(self.anchors) < 10000:
                self.anchors.setdefault(values[key], self.length)
        if tag == "title":
            self.in_title = True
        if tag == "pre":
            self.pre += 1
        if tag in {
            "br",
            "p",
            "div",
            "pre",
            "li",
            "tr",
            "hr",
            "dt",
            "dd",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
        }:
            self.append("\n")
        elif tag in {"td", "th"}:
            self.append("\t")
        if tag in {"a", "frame", "iframe"}:
            href = values.get("href" if tag == "a" else "src")
            if href:
                self.link_count += 1
                if len(self.links) < 10000:
                    self.links.append([href, self.length, self.length])
                    if tag == "a":
                        self.active_link = len(self.links) - 1

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag == "a" and self.active_link is not None:
            self.links[self.active_link][2] = self.length
            self.active_link = None
        if tag == "title":
            self.in_title = False
        if tag == "pre":
            self.pre = max(0, self.pre - 1)
        if tag in {"p", "div", "pre", "li", "tr", "dt", "dd", "h1", "h2", "h3"}:
            self.append("\n")

    def handle_data(self, data):
        if self.hidden:
            return
        if self.in_title:
            self.title = (self.title + data)[:300]
            return
        self.append(data if self.pre else re.sub(r"\s+", " ", data))


def read_html(path: Path) -> tuple[ManualHTML, str, str]:
    fd = -1
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_HTML_BYTES:
            raise RequestFailure("invalid_page", "HTML must be a regular file up to 8 MiB")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            raw = handle.read(MAX_HTML_BYTES + 1)
        if len(raw) > MAX_HTML_BYTES:
            raise RequestFailure("invalid_page", "HTML exceeds 8 MiB")
    except OSError as exc:
        raise RequestFailure("manual_unavailable", str(exc)) from exc
    finally:
        if fd >= 0:
            os.close(fd)
    encoding = "utf-8-sig"
    match = re.search(rb'charset\s*=\s*["\x27]?([a-zA-Z0-9_-]+)', raw[:8192], re.I)
    if match:
        encoding = match[1].decode("ascii")
    try:
        text = raw.decode(encoding)
    except (LookupError, UnicodeError):
        encoding = "utf-8-replacement"
        text = raw.decode("utf-8", errors="replace")
    parser = ManualHTML()
    for start in range(0, len(text), 16384):
        parser.feed(text[start : start + 16384])
        if parser.truncated:
            break
    if not parser.truncated:
        parser.close()
    return parser, hashlib.sha256(raw).hexdigest(), encoding


def read_page(doc: Path, reference: str, offset: int, max_chars: int, expected: str | None):
    path, anchor = local_page(doc, reference)
    parser, digest, encoding = read_html(path)
    if expected and expected != digest:
        raise RequestFailure("manual_changed", "HTML changed; restart reading with offset=0")
    if anchor and anchor not in parser.anchors:
        raise RequestFailure(
            "anchor_not_found", "Anchor absent from extracted text; read page without #anchor"
        )
    text = "".join(parser.parts)
    start = parser.anchors.get(anchor, 0) + offset
    if start > len(text):
        raise RequestFailure("invalid_params", "offset exceeds extracted page text")
    end = min(len(text), start + max_chars)
    links = []
    visible_links = [
        link for link in parser.links if start <= link[1] < end or (link[1] == end == len(text))
    ]
    for href, position, label_end in visible_links:
        if len(links) >= 30:
            break
        try:
            linked, fragment = local_page(doc, href, path)
        except RequestFailure:
            continue
        # Return verbatim encoded references, so spaces and literal # in filenames round-trip.
        page = quote(linked.relative_to(doc).as_posix(), safe="/")
        if fragment:
            page += "#" + quote(fragment, safe="")
        if len(page) <= 1024 and len(links) < 30:
            links.append(
                {"page": page, "label": text[position : min(label_end, position + 80)].strip()}
            )
    return {
        "ok": True,
        "page": reference,
        "source_path": str(path),
        "title": parser.title.strip(),
        "sha256": digest,
        "encoding": encoding,
        "text": text[start:end],
        "offset": offset,
        "next_offset": offset + end - start if end < len(text) else None,
        "extracted_chars": len(text),
        "extraction_truncated": parser.truncated,
        "complete": end == len(text) and not parser.truncated,
        "links": links,
        "links_truncated": parser.link_count > len(parser.links) or len(visible_links) > len(links),
    }
