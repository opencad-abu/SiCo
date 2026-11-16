from __future__ import annotations

from cadgui.fonts import mono_family
from sico_ui.code_highlight import (
    CODE_COLORS,
    MAX_HIGHLIGHT_CHARS,
    code_block_html,
    guess_language,
    highlight,
    language_of,
    split_fences,
    table_safe_markdown,
)


def test_table_safe_markdown_separates_tables_from_paragraphs():
    """紧贴段落的管道表格补空行成表；围栏代码与正文不受影响。"""

    assert table_safe_markdown("器件概览\n| A | B |\n|---|---|\n| 1 | 2 |\n接下来") == (
        "器件概览\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n接下来"
    )
    assert table_safe_markdown("说明\n\n| A | B |\n|---|---|\n\n结束") == (
        "说明\n\n| A | B |\n|---|---|\n\n结束"
    )
    assert table_safe_markdown("只有正文\n第二行") == "只有正文\n第二行"
    assert table_safe_markdown("```\n| A | B |\n|---|---|\n```\n后文") == (
        "```\n| A | B |\n|---|---|\n```\n后文"
    )
    assert table_safe_markdown("标题\n| A | B |\n|---|---|\n| 1 | 2 |").endswith("| 1 | 2 |\n")


def test_split_fences_recognizes_aliases_and_plain_blocks():
    markdown = (
        "说明文字\n\n"
        "```python\nx = 1\n```\n\n"
        "```sh\necho hi\n```\n"
        "```SKILL\nprocedure(foo() t)\n```\n"
        "```tclsh\nputs $x\n```\n"
        "```output\nhello\n```\n"
    )
    parts = split_fences(markdown)
    assert parts[0] == ("text", "说明文字\n\n")
    assert [part[0] for part in parts[1:]] == ["code", "text", "code", "code", "code", "code"]
    assert [part[1] for part in parts if part[0] == "code"] == [
        "python", "shell", "skill", "tcl", ""]
    assert [part[2] for part in parts if part[0] == "code"] == [
        "x = 1\n", "echo hi\n", "procedure(foo() t)\n", "puts $x\n", "hello\n"]


def test_split_fences_keeps_an_unclosed_fence():
    parts = split_fences("前文\n```python\nx = 1\ny = 2")
    assert parts == [("text", "前文\n"), ("code", "python", "x = 1\ny = 2")]
    # 围栏最长为三个前导空格；缩进更深的 ``` 只当普通文本。
    assert split_fences("    ```python\nx\n") == [("text", "    ```python\nx\n")]


def test_language_of_normalizes_names_and_versions():
    assert language_of("PYTHON") == "python"
    assert language_of("py3") == "python"
    assert language_of("bash") == "shell"
    assert language_of("ils") == "skill"
    assert language_of("tclsh") == "tcl"
    assert language_of("ruby") == ""
    assert language_of("") == ""


def test_highlight_colors_keywords_comments_strings_and_numbers():
    python = highlight("def f():\n    s = \"hi\"  # note 42", "python")
    assert f"color: {CODE_COLORS['keyword']}" in python       # def
    assert f"color: {CODE_COLORS['string']}" in python        # "hi"
    assert f"color: {CODE_COLORS['comment']}" in python       # # note 42
    skill = highlight("let((x 1) /* c */ x)", "skill")
    assert f"color: {CODE_COLORS['keyword']}" in skill        # let
    assert f"color: {CODE_COLORS['comment']}" in skill        # /* c */
    shell = highlight("if [ -f \"$file\" ]; then echo done; fi", "shell")
    assert f"color: {CODE_COLORS['keyword']}" in shell        # if/then/fi
    assert f"color: {CODE_COLORS['string']}" in shell         # "$file"
    # Tcl：花括号是字面量（不替换），$名字按内建规则识别，保持正文色。
    tcl = highlight("set name value\nputs $name", "tcl")
    assert f"color: {CODE_COLORS['keyword']}" in tcl          # set/puts
    assert f'<span style="color: {CODE_COLORS["builtin"]}">$name</span>' in tcl


def test_highlight_escapes_html_and_keeps_unknown_languages_plain():
    rendered = highlight("<script>alert(1)</script>", "python")
    assert "<script>" not in rendered and "&lt;script&gt;" in rendered
    assert highlight("a < b", "ruby") == "a &lt; b"
    assert highlight("plain", "") == "plain"
    # 代码块外壳保留等宽与浅底，正文仍受控。
    block = code_block_html("print(1)", "python")
    assert block.startswith("<pre") and (
        f"font-family: '{mono_family()}', monospace" in block)
    assert f"color: {CODE_COLORS['builtin']}" in block


def test_highlight_degrades_for_oversized_blocks():
    oversized = "x" * (MAX_HIGHLIGHT_CHARS + 1)
    assert highlight(oversized, "python") == oversized
    many_lines = "\n".join(["x = 1"] * 2100)
    assert highlight(many_lines, "python") == many_lines


def test_guess_language_from_shebang_command_and_path():
    assert guess_language("#!/usr/bin/env python3\nprint(1)") == "python"
    assert guess_language("#!/bin/bash -e\necho hi") == "shell"
    assert guess_language("/bin/bash -lc 'echo hi'") == "shell"
    assert guess_language("tclsh run.tcl") == "tcl"
    assert guess_language("tools/run_flow.ils") == "skill"
    assert guess_language("create_report.py") == "python"
    assert guess_language("virtuoso -nograph") == ""
    assert guess_language("") == ""
