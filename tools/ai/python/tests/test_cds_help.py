from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from cadai.mcp import TOOLS, McpServer
from cadai.socket_server import RequestFailure
from cadai.tool_help import cds_help
from cadai.tool_help_schema import DOC_ENV_NAMES, discover


@pytest.fixture
def manual(tmp_path, monkeypatch):
    for name in DOC_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "SPECTRE23.10.538"
    (root / "bin").mkdir(parents=True)
    executable = root / "bin/spectre"
    executable.write_text("#!/bin/sh\ntouch SHOULD_NOT_RUN\n")
    executable.chmod(0o755)
    monkeypatch.setenv("PATH", str(root / "bin"))
    doc = root / "doc/guide"
    doc.mkdir(parents=True)
    (doc / "index.html").write_text("""<html><head><title>Test &amp; Guide</title>
    <script>SECRET_SCRIPT</script><style>SECRET_STYLE</style></head><body>
    <h1 id="noise">Noise Analysis</h1><p>Learn &lt;noise&gt; &amp; more.</p>
    <pre>procedure(test()\n  t)\n</pre>
    <a href="chapter.html#details">Chapter details</a>
    <a href="https://example.com">External</a><a href="javascript:evil()">bad</a>
    <a href="../../outside.html">escape</a><a href="manual.pdf">PDF</a>
    <iframe src="chapter.html"></iframe></body></html>""")
    (doc / "chapter.html").write_text('<h1 id="details">Details</h1><p>Noise answer</p>')
    (doc / "code.js").write_text("Noise JS MUST_NOT_MATCH")
    (doc / "manual.pdf").write_text("Noise PDF MUST_NOT_MATCH")
    return root


def test_discover_which_bin_parent_doc_without_executing(manual):
    found = discover()
    assert len(found) == 1
    assert found[0]["doc_root"] == str(manual / "doc")
    assert found[0]["discovered_by"] == ["PATH:" + str(manual / "bin/spectre")]
    assert not (Path.cwd() / "SHOULD_NOT_RUN").exists()


def test_symlink_entry_and_path_precedence(manual, tmp_path, monkeypatch):
    link_bin = tmp_path / "site/bin"
    link_bin.mkdir(parents=True)
    (link_bin / "spectre").symlink_to(manual / "bin/spectre")
    other = tmp_path / "SPECTRE_OLD"
    (other / "doc").mkdir(parents=True)
    monkeypatch.setenv("PATH", str(link_bin))
    monkeypatch.setenv("SPECTRE_HOME", str(other))
    result = cds_help({"action": "files", "tool": "spectre"})
    assert result["source"]["install_root"] == str(manual)
    assert len(cds_help({"tool": "spectre"})["installations"]) == 2


def test_search_only_html_and_paths_roundtrip(manual):
    result = cds_help({"action": "search", "tool": "spectre", "query": "noise", "manual": "guide"})
    assert result["ok"]
    assert all(item["page"].endswith(".html") for item in result["results"])
    assert not any("MUST_NOT_MATCH" in item["html_source_excerpt"] for item in result["results"])
    page = result["results"][0]["page"]
    read = cds_help({"action": "read", "tool": "spectre", "page": page})
    assert read["ok"] and read["source"]["installation"] == manual.name


def test_read_text_entities_pre_links_and_anchor(manual):
    result = cds_help({"action": "read", "tool": "spectre", "page": "guide/index.html#noise"})
    assert result["title"] == "Test & Guide"
    assert "Learn <noise> & more." in result["text"]
    assert "procedure(test()\n  t)\n" in result["text"]
    assert "SECRET_SCRIPT" not in result["text"] and "SECRET_STYLE" not in result["text"]
    assert {link["page"] for link in result["links"]} == {
        "guide/chapter.html#details",
        "guide/chapter.html",
    }
    assert result["links_truncated"]


def test_pagination_and_hash_detect_modified_page(manual):
    request = {"action": "read", "tool": "spectre", "page": "guide/index.html", "max_chars": 15}
    first = cds_help(request)
    second = cds_help({**request, "offset": first["next_offset"], "sha256": first["sha256"]})
    whole = cds_help({**request, "max_chars": 6000})
    assert first["text"] + second["text"] == whole["text"][:30]
    (manual / "doc/guide/index.html").write_text("changed")
    with pytest.raises(RequestFailure) as error:
        cds_help({**request, "sha256": first["sha256"]})
    assert error.value.code == "manual_changed"


@pytest.mark.parametrize(
    "page",
    [
        "../outside.html",
        "%2e%2e/outside.html",
        "/etc/passwd",
        "https://example.com/manual.html",
        "guide/code.js",
        "guide/index.html?query=1",
        "guide/index.html#absent",
    ],
)
def test_reject_invalid_page(manual, page):
    with pytest.raises(RequestFailure):
        cds_help({"action": "read", "tool": "spectre", "page": page})


def test_symlink_escape_special_and_large_file_rejected(manual, tmp_path):
    outside = tmp_path / "outside.html"
    outside.write_text("private")
    target = manual / "doc/escape.html"
    target.symlink_to(outside)
    os.mkfifo(manual / "doc/fifo.html")
    (manual / "doc/large.html").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
    for page in ("escape.html", "fifo.html", "large.html"):
        with pytest.raises(RequestFailure):
            cds_help({"action": "read", "tool": "spectre", "page": page})
    with pytest.raises(RequestFailure):
        cds_help({"action": "files", "tool": "spectre", "manual": "../.."})


def test_explicit_unrecognized_product_and_non_utf8_html(manual, monkeypatch):
    monkeypatch.setenv("PATH", "/no/tools")
    (manual / "doc/latin.html").write_bytes(b'<meta charset="windows-1252"><p>caf\xe9</p>')
    result = cds_help({"action": "read", "install_root": str(manual), "page": "latin.html"})
    assert "café" in result["text"] and result["encoding"] == "windows-1252"


@pytest.mark.parametrize(
    "args",
    [
        {"action": []},
        {"tool": []},
        {"action": "search", "tool": "spectre"},
        {"action": "read", "page": "a", "offset": True},
        {"action": "files", "query": "noise"},
        {"action": "read", "page": "a", "sha256": "x"},
        {"action": "search", "query": "a\nb"},
        {"shell": True},
        {"install_root": "relative"},
        {"tool": "spectre", "install_root": "/something"},
    ],
)
def test_arguments(manual, args):
    with pytest.raises(RequestFailure) as error:
        cds_help(args)
    assert error.value.code == "invalid_params"


def test_oversized_numeric_arguments_are_clamped(manual):
    result = cds_help({
        "action": "read",
        "install_root": str(manual),
        "page": "guide/index.html",
        "max_chars": 6001,
    })
    assert result["page"] == "guide/index.html"


def test_mcp_is_local(manual):
    class Client:
        def call(self, *args):
            pytest.fail("must not contact Virtuoso")

    request = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "cds_help",
            "arguments": {"action": "read", "tool": "spectre", "page": "guide/index.html"},
        },
    }
    result = McpServer(Client()).handle(request)["result"]
    assert not result["isError"] and json.loads(result["content"][0]["text"])["ok"]
    assert next(t for t in TOOLS if t["name"] == "cds_help")["annotations"]["readOnlyHint"]


def test_legacy_name_dispatches_but_is_not_advertised(manual):
    """Old records and prompts keep working; the wire list carries one canonical name."""
    names = [tool["name"] for tool in TOOLS]
    assert "cds_help" in names and "tool_help" not in names

    class Client:
        def call(self, *args):
            pytest.fail("must not contact Virtuoso")

    def call(server, name):
        return server.handle({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": {
                "action": "read", "tool": "spectre", "page": "guide/index.html"}},
        })["result"]

    legacy = call(McpServer(Client()), "tool_help")
    canonical = call(McpServer(Client()), "cds_help")
    assert not legacy["isError"] and json.loads(legacy["content"][0]["text"])["ok"]
    assert legacy["content"] == canonical["content"]


def test_legacy_name_resolves_before_profile_checks(manual):
    """The alias must not bypass the canonical tool's profile boundary."""
    class Client:
        def call(self, *args):
            pytest.fail("must not contact Virtuoso")

    server = McpServer(Client(), profile="verification")
    for name in ("cds_help", "tool_help"):
        response = server.handle({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": {"action": "discover"}},
        })
        assert response["error"]["code"] == -32602


def test_output_bound_and_search_truncation(manual):
    (manual / "doc/guide/long.html").write_text("<p>" + "界" * 10000 + "</p>")
    read = cds_help({"action": "read", "tool": "spectre", "page": "guide/long.html"})
    assert not read["complete"] and len(json.dumps(read, ensure_ascii=True).encode()) < 65536
    result = cds_help({"action": "search", "tool": "spectre", "query": "noise", "max_results": 1})
    assert result["truncated"] and not result["complete"]


def test_escaped_filename_link_roundtrip(manual):
    (manual / "doc/guide/a # b.html").write_text("<p>special filename</p>")
    (manual / "doc/guide/links.html").write_text('<a href="a%20%23%20b.html">Special</a>')
    result = cds_help({"action": "read", "tool": "spectre", "page": "guide/links.html"})
    page = result["links"][0]["page"]
    assert (
        "special filename" in cds_help({"action": "read", "tool": "spectre", "page": page})["text"]
    )


def test_nested_virtuoso_entry_prefers_product_doc_over_wrapper_doc(manual, monkeypatch):
    binary = manual / "tools/dfII/bin/virtuoso"
    binary.parent.mkdir(parents=True)
    wrapper = manual / "share/bin/wrapper"
    wrapper.parent.mkdir(parents=True)
    (manual / "share/doc/third_party").mkdir(parents=True)
    wrapper.write_text("#!/bin/sh\nexit 1\n")
    wrapper.chmod(0o755)
    binary.symlink_to(wrapper)
    monkeypatch.setenv("PATH", str(binary.parent))
    result = cds_help({"action": "files", "tool": "virtuoso"})
    assert result["source"]["install_root"] == str(manual)


def test_links_follow_text_pagination(manual):
    (manual / "doc/guide/paged.html").write_text(
        "<p>" + "a" * 100 + '</p><a href="chapter.html">The chapter</a>'
    )
    first = cds_help(
        {"action": "read", "tool": "spectre", "page": "guide/paged.html", "max_chars": 50}
    )
    assert not first["links"]
    second = cds_help(
        {"action": "read", "tool": "spectre", "page": "guide/paged.html", "offset": 100}
    )
    assert second["links"][0]["label"] == "The chapter"


def test_manual_discovery_environment_is_forwarded_to_both_clients(manual):
    from cadai.launch import build_claude_mcp_config, build_codex_command
    from cadai.runtime import RuntimePaths

    env = {"PATH": str(manual / "bin"), "SPECTRE_HOME": str(manual)}
    runtime = RuntimePaths(manual, manual / "bridge.sock", manual / "spool")
    config = json.loads(
        build_claude_mcp_config(
            "/python", manual / "sico-ai", runtime, "test-token", 30, environment=env
        )
    )
    child = config["mcpServers"]["sico_ic"]["env"]
    assert child["PATH"] == env["PATH"] and child["SPECTRE_HOME"] == str(manual)
    command = build_codex_command("/codex", "/python", manual, manual, 30, environment=env)
    forwarded = next(
        value.split("=", 1)[1]
        for value in command
        if value.startswith("mcp_servers.sico_ic.env_vars=")
    )
    assert {"PATH", "SPECTRE_HOME"}.issubset(json.loads(forwarded))
    enabled = next(
        value.split("=", 1)[1]
        for value in command
        if value.startswith("mcp_servers.sico_ic.enabled_tools=")
    )
    assert "cds_help" in json.loads(enabled)


def test_discovery_response_is_bounded(monkeypatch):
    import importlib

    module = importlib.import_module("cadai.tool_help")
    monkeypatch.setattr(module, "discover", lambda: [{"install_root": "x" * 4000}] * 30)
    result = cds_help({})
    assert result["truncated"]
    assert len(json.dumps(result).encode()) <= 65536
