"""Bounded syntax highlighting for code blocks shown in the detail page.

展示层专用：按 markdown 围栏拆分内容，识别 python / shell / skill / tcl 四种脚本，
用一组有界正则给注释、字符串、数字、关键字、内建着色。它不是解析器，也不参与
``check_skill`` 的预检策略：识别不出的语言、超长内容、未闭合围栏都按纯文本渲染。
"""

from __future__ import annotations

import re
from html import escape

from .theme import (
    CODE_BLOCK_BACKGROUND,
    CODE_BUILTIN,
    CODE_COMMENT,
    CODE_KEYWORD,
    CODE_NUMBER,
    CODE_STRING,
)

__all__ = (
    "CODE_COLORS", "LANGUAGES", "code_block_html", "guess_language", "highlight",
    "language_of", "split_fences", "table_safe_markdown",
)

MAX_HIGHLIGHT_CHARS = 64 * 1024
MAX_HIGHLIGHT_LINES = 2000

LANGUAGES = ("python", "shell", "skill", "tcl")

CODE_COLORS = {
    "keyword": CODE_KEYWORD,
    "string": CODE_STRING,
    "comment": CODE_COMMENT,
    "number": CODE_NUMBER,
    "builtin": CODE_BUILTIN,
}

# 围栏语言标记与解释器/后缀的别名；均为小写比较。
ALIASES = {
    "python": "python", "py": "python", "python3": "python", "py3": "python",
    "shell": "shell", "sh": "shell", "bash": "shell", "zsh": "shell", "ksh": "shell",
    "dash": "shell", "csh": "shell", "tcsh": "shell",
    "skill": "skill", "il": "skill", "ils": "skill", "skill++": "skill",
    "tcl": "tcl", "tclsh": "tcl", "wish": "tcl",
}

_NUMBER = r"\b\d+(?:\.\d+)?\b"

_RULES = {
    "python": (
        ("comment", r"#[^\n]*"),
        ("string", r"\"\"\".*?\"\"\"|'''.*?'''"
                   r"|\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'"),
        ("number", _NUMBER),
        ("keyword", r"\b(?:def|class|if|elif|else|for|while|try|except|finally|with|"
                    r"import|from|as|return|yield|raise|global|nonlocal|assert|pass|"
                    r"break|continue|and|or|not|in|is|lambda|None|True|False|async|"
                    r"await|del)\b"),
        ("builtin", r"\b(?:print|len|range|enumerate|zip|open|type|isinstance|int|"
                    r"float|str|bool|list|dict|set|tuple|sum|min|max|sorted|abs|round|"
                    r"getattr|setattr|hasattr|Exception|ValueError|RuntimeError|"
                    r"KeyError|OSError|TypeError)\b"),
    ),
    "shell": (
        ("comment", r"#[^\n]*"),
        ("string", r"'(?:[^']*)'|\"(?:\\.|[^\"\\])*\"|\$'(?:\\.|[^'\\])*'"),
        ("builtin", r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*"),
        ("number", _NUMBER),
        ("keyword", r"\b(?:if|then|elif|else|fi|for|while|until|do|done|case|esac|"
                    r"function|return|exit|export|local|readonly|source|set|unset|shift|"
                    r"in|break|continue|declare|alias|trap|eval|exec|time)\b"),
        ("builtin", r"\b(?:echo|printf|read|cd|pwd|test|true|false|cat|grep|sed|awk|"
                    r"ls|cp|mv|rm|mkdir|chmod|which|command|type|sleep|env)\b"),
    ),
    "skill": (
        ("comment", r"/\*.*?\*/|;[^\n]*"),
        ("string", r"\"(?:\\.|[^\"\\])*\""),
        ("number", _NUMBER),
        ("keyword", r"\b(?:procedure|let|prog|setq|set|if|when|unless|foreach|foreachs|"
                    r"for|while|case|cond|return|and|or|not|nil|t|else|quote|function|"
                    r"lambda)\b"),
        ("builtin", r"\b(?:car|cdr|cons|list|append|length|sprintf|printf|println|"
                    r"fprintf|getq|putprop|get|setof|mapcar|mapc|reverse|nth|member|"
                    r"assoc|hiGetCurrentWindow|hiSetCurrentWindow|dbOpenCellViewByType|"
                    r"dbSave|dbClose|sdbFindByName|ipcBeginProcess)\b"),
    ),
    "tcl": (
        ("comment", r"#[^\n]*"),
        ("string", r"\"(?:\\.|[^\"\\])*\"|\{[^{}]*\}"),
        ("builtin", r"\$\{[^}]*\}|\$[A-Za-z_][A-Za-z0-9_]*(?:\([^)]*\))?|\[[^\[\]\n]*\]"),
        ("number", _NUMBER),
        ("keyword", r"\b(?:proc|set|unset|if|else|elseif|for|foreach|while|switch|case|"
                    r"default|break|continue|return|source|exec|eval|upvar|uplevel|global|"
                    r"variable|namespace|catch|error|incr|list|lappend|string|dict|array|"
                    r"expr|puts|open|close|read|gets|format|regexp|regsub)\b"),
    ),
}


def _compile(language):
    rules = _RULES[language]
    parts = [f"(?P<t{index}>{pattern})" for index, (_name, pattern) in enumerate(rules)]
    return re.compile("|".join(parts), re.DOTALL), [name for name, _pattern in rules]


_COMPILED = {language: _compile(language) for language in LANGUAGES}

# ``` / ~~~ 围栏：最多三个前导空格，语言标记取第一个词。
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*(\S*)[^\n]*$")
_FENCE_CLOSE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*$")


def language_of(tag):
    """Normalize a fence tag or interpreter name; ``""`` means plain text."""

    name = str(tag or "").strip().lower()
    if not name:
        return ""
    if name in ALIASES:
        return ALIASES[name]
    stripped = re.sub(r"[0-9.]+$", "", name)
    return ALIASES.get(stripped, "")


def split_fences(markdown):
    """Split markdown into ``("text", text)`` and ``("code", language, code)`` parts.

    未闭合的围栏按“代码块延续到文本结尾”处理，内容不丢。
    """

    parts, text_lines, code_lines = [], [], []
    fence = None
    for line in str(markdown or "").splitlines(keepends=True):
        if fence is None:
            match = _FENCE_OPEN.match(line.rstrip("\n"))
            if match:
                fence = (match.group(1)[0], len(match.group(1)), language_of(match.group(2)))
                if text_lines:
                    parts.append(("text", "".join(text_lines)))
                    text_lines = []
                continue
            text_lines.append(line)
            continue
        marker, length, language = fence
        closing = _FENCE_CLOSE.match(line.rstrip("\n"))
        if closing and closing.group(1)[0] == marker and len(closing.group(1)) >= length:
            parts.append(("code", language, "".join(code_lines)))
            code_lines, fence = [], None
            continue
        code_lines.append(line)
    if fence is not None:
        parts.append(("code", fence[2], "".join(code_lines)))
    if text_lines:
        parts.append(("text", "".join(text_lines)))
    return parts


def highlight(code, language, *, colors=None):
    """Return escaped HTML with colored spans; unknown or oversized code stays plain."""

    source = str(code or "")
    rules = _COMPILED.get(language_of(language) or str(language or "").lower())
    if rules is None or len(source) > MAX_HIGHLIGHT_CHARS \
            or source.count("\n") > MAX_HIGHLIGHT_LINES:
        return escape(source)
    matcher, names = rules
    palette = colors or CODE_COLORS
    parts, position = [], 0
    for match in matcher.finditer(source):
        if match.start() > position:
            parts.append(escape(source[position:match.start()]))
        group = match.lastgroup
        token = names[int(str(group)[1:])] if group else ""
        color = palette.get(token)
        text = escape(match.group(0))
        parts.append(f'<span style="color: {color}">{text}</span>' if color else text)
        position = match.end()
    if position < len(source):
        parts.append(escape(source[position:]))
    return "".join(parts)


def code_block_html(code, language, *, colors=None):
    """One highlighted code block: monospace, light background, wrapped lines."""

    body = highlight(code, language, colors=colors)
    return ('<pre style="font-family: monospace; white-space: pre-wrap; '
            f'background-color: {CODE_BLOCK_BACKGROUND}; margin: 6px 0;">' + body + "</pre>")


def guess_language(source):
    """Best-effort language of a command line or path; ``""`` when unknown."""

    text = str(source or "").strip()
    if not text:
        return ""
    first = text.splitlines()[0].strip()
    candidate = ""
    if first.startswith("#!"):
        words = first[2:].split()
        if words:
            name = words[-1] if words[0].endswith("env") else words[0]
            candidate = name.rsplit("/", 1)[-1]
    else:
        command = first.split()[0] if first.split() else ""
        candidate = command.rsplit("/", 1)[-1]
        if "." in candidate:
            # 文件名优先按后缀判断，命令名（python3 / bash / tclsh）按解释器名判断。
            candidate = candidate.rsplit(".", 1)[-1]
    if not candidate:
        return ""
    normalized = language_of(candidate)
    if normalized:
        return normalized
    return ALIASES.get(re.sub(r"[0-9.]+$", "", candidate.lower()), "")

TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")


def table_safe_markdown(markdown):
    """把紧贴段落的管道表格独立成段。

    模型常写“标题\n| 列 | 列 |\n|---|---|”，Qt 的 markdown 解析要求表格自成
    一块，否则整段退化成竖线文字。这里只补空行、不改内容（围栏代码内不动）。
    """

    lines = str(markdown or "").splitlines()
    result, fence, in_table = [], None, False
    for line in lines:
        stripped = line.strip()
        if fence is None and stripped.startswith(("```", "~~~")):
            if in_table:
                result.append("")
                in_table = False
            fence = stripped[:3]
            result.append(line)
            continue
        if fence is not None:
            result.append(line)
            if stripped.startswith(fence):
                fence = None
            continue
        if TABLE_ROW.match(line):
            if not in_table and result and result[-1].strip():
                result.append("")
            in_table = True
        elif in_table:
            if stripped:
                result.append("")
            in_table = False
        result.append(line)
    if in_table:
        result.append("")
    return "\n".join(result)
