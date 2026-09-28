"""Shared SKILL lexical tokens and parenthesized frames for source audits.

Preserves character offsets, comments, quoting and algebraic call adjacency. This
is a structural reader, not a complete SKILL evaluator or syntax validator.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    start: int
    end: int
    line: int


@dataclass
class Frame:
    open_token: Token
    parent: Frame | None
    previous: Token | None
    quoted: bool
    children: list[Token | Frame] = field(default_factory=list)
    close_token: Token | None = None

    @property
    def algebraic_name(self) -> str | None:
        previous = self.previous
        if (
            previous is not None
            and previous.kind == "name"
            and previous.end == self.open_token.start
        ):
            return previous.text
        return None

    @property
    def first_name(self) -> Token | None:
        if self.children and isinstance(self.children[0], Token):
            token = self.children[0]
            if token.kind == "name":
                return token
        return None

    @property
    def operator(self) -> str | None:
        return self.algebraic_name or (
            self.first_name.text if self.first_name is not None else None
        )


def tokenize(source: str) -> list[Token]:
    tokens: list[Token] = []
    index = 0
    line = 1
    length = len(source)
    while index < length:
        start = index
        start_line = line
        character = source[index]
        if character.isspace():
            while index < length and source[index].isspace():
                if source[index] == "\n":
                    line += 1
                index += 1
            tokens.append(Token("space", source[start:index], start, index, start_line))
        elif character == ";":
            while index < length and source[index] != "\n":
                index += 1
            tokens.append(Token("comment", source[start:index], start, index, start_line))
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            index = length if end < 0 else end + 2
            line += source[start:index].count("\n")
            tokens.append(Token("comment", source[start:index], start, index, start_line))
        elif character == '"':
            index += 1
            escaped = False
            while index < length:
                current = source[index]
                if current == "\n":
                    line += 1
                index += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    break
            tokens.append(Token("string", source[start:index], start, index, start_line))
        elif character == "(":
            index += 1
            tokens.append(Token("open", character, start, index, start_line))
        elif character == ")":
            index += 1
            tokens.append(Token("close", character, start, index, start_line))
        elif character in {"'", "`"}:
            index += 1
            tokens.append(Token("quote", character, start, index, start_line))
        elif character.isalpha() or character == "_":
            index += 1
            while index < length and (
                source[index].isalnum() or source[index] in {"_", "$"}
            ):
                index += 1
            tokens.append(Token("name", source[start:index], start, index, start_line))
        else:
            index += 1
            tokens.append(Token("other", character, start, index, start_line))
    return tokens


def parse(source: str) -> tuple[list[Token], list[Frame]]:
    tokens = tokenize(source)
    frames: list[Frame] = []
    stack: list[Frame] = []
    previous: Token | None = None
    for token in tokens:
        if token.kind in {"space", "comment"}:
            continue
        if token.kind == "open":
            inherited_quote = stack[-1].quoted if stack else False
            frame = Frame(
                open_token=token,
                parent=stack[-1] if stack else None,
                previous=previous,
                quoted=inherited_quote
                or (previous is not None and previous.kind == "quote"),
            )
            if stack:
                stack[-1].children.append(frame)
            frames.append(frame)
            stack.append(frame)
        elif token.kind == "close":
            if stack:
                stack[-1].close_token = token
                stack.pop()
        elif stack:
            stack[-1].children.append(token)
        previous = token
    return tokens, frames
