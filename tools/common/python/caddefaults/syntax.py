"""Line and fixed-section syntax shared by defaults text readers."""

from __future__ import annotations

import re

from .source import Source


def rows(source: Source, comments: tuple[str, ...] = ("#",)):
    for number, raw in enumerate(source.text.splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith(comments):
            if any(ord(char) < 32 and char != "\t" for char in line):
                raise source.error(number, "Control characters are not supported")
            yield number, line


def sections(source: Source, allowed: set[str], *, comments=("#",)):
    result = {}
    current = None
    for number, line in rows(source, comments):
        match = re.fullmatch(r"\[([a-z_]+(?:\.[a-z_]+)*)\]", line)
        if match:
            current = match[1]
            if current not in allowed or current in result:
                raise source.error(number, f"Unknown or repeated section [{current}]")
            result[current] = []
        elif current is None:
            raise source.error(number, "Expected a named defaults section")
        else:
            result[current].append((number, line))
    return result
