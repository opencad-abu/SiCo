"""Synthetic local documents and a controlled OpenAI Docs MCP wire fixture."""

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def local(root, identity="local"):
    root.mkdir(parents=True, exist_ok=True)
    return dict(
        id=identity,
        name="工艺说明",
        type="local",
        path=str(root),
        source="project",
        enabled=True,
        formats=["text", "markdown", "pdf"],
        include=[],
        exclude=[],
        update="manual",
    )


def remote(url, **extra):
    return dict(
        id="remote",
        name="Remote docs",
        type="remote",
        adapter="openai_docs_mcp",
        url=url,
        collection="all",
        source="project",
        enabled=True,
        timeout_sec=8,
        **extra,
    )


def jedai(url, identity="jedai", **extra):
    row = dict(
        id=identity,
        name="JedAI 知识库",
        type="remote",
        adapter="jedai_aai_mcp",
        url=url,
        collection="ask-docs-26.10",
        source="project",
        enabled=True,
        timeout_sec=8,
    )
    row.update(extra)
    return row


def manifest(*sources):
    return {"version": 2, "knowledge_sources": list(sources)}


def pdf_document():
    stream = b"BT /F1 12 Tf 20 300 Td (nmos_1v8 gate voltage) Tj ET"
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R 6 0 R] /Count 2 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 400] /Resources << >> >>",
    ]
    raw, offsets = b"%PDF-1.4\n", [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(raw))
        raw += str(number).encode() + b" 0 obj\n" + obj + b"\nendobj\n"
    start = len(raw)
    raw += b"xref\n0 7\n0000000000 65535 f \n"
    raw += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    return (
        raw
        + b"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n"
        + str(start).encode()
        + b"\n%%EOF\n"
    )


@contextmanager
def docs_server(*, bearer=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.seen.append((value, dict(self.headers)))
            if self.server.delay:
                time.sleep(self.server.delay)
            if bearer and self.headers.get("Authorization") != "Bearer " + bearer:
                self.send_response(401)
                self.end_headers()
                return
            method = value["method"]
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "controlled-docs", "version": "1"},
                }
            elif method == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            else:
                call = value["params"]
                if call["name"] == "search_openai_docs":
                    result = {
                        "content": [
                            {"type": "text", "text": json.dumps({"hits": self.server.hits})}
                        ]
                    }
                else:
                    text = self.server.documents.get(call["arguments"]["url"])
                    result = {
                        "content": [{"type": "text", "text": text or "No doc"}],
                        "isError": text is None,
                    }
            raw = json.dumps({"jsonrpc": "2.0", "id": value["id"], "result": result}).encode()
            if self.server.sse:
                raw = b"event: message\ndata: " + raw + b"\n\n"
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/event-stream" if self.server.sse else "application/json"
            )
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    url = "https://learn.chatgpt.com/docs/extend/mcp"
    server.documents = {
        url: "# MCP tools\nSTDIO 和 Streamable HTTP 支持。\nDocs cannot grant PDK eligibility."
    }
    server.hits = [{"url": url, "hierarchy": {"lvl1": "MCP docs"}}]
    server.seen, server.delay, server.sse = [], 0, False
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server, remote("http://127.0.0.1:" + str(server.server_port) + "/mcp")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


@contextmanager
def jedai_server(*, bearer=None):
    """Controlled AAI MCP wire: category listing and ranked vector-search results."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.seen.append((value, dict(self.headers)))
            if self.server.delay:
                time.sleep(self.server.delay)
            if bearer and self.headers.get("Authorization") != "Bearer " + bearer:
                self.send_response(401)
                self.end_headers()
                return
            method = value["method"]
            if method == "initialize":
                result = {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "controlled-jedai", "version": "1"},
                }
            elif method == "notifications/initialized":
                self.send_response(202)
                self.end_headers()
                return
            else:
                call = value["params"]
                if call["name"] == "list_document_categories":
                    text = json.dumps([{"category": name} for name in self.server.categories])
                elif call["name"] == "search_jedai_knowledge":
                    categories = call["arguments"].get("categories", [])
                    text = json.dumps({"selected_categories": categories,
                                       "results": self.server.results})
                else:
                    self.send_response(500)
                    self.end_headers()
                    return
                result = {"content": [{"type": "text", "text": text}], "isError": False}
            raw = json.dumps({"jsonrpc": "2.0", "id": value["id"], "result": result}).encode()
            if self.server.sse:
                raw = b"event: message\ndata: " + raw + b"\n\n"
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/event-stream" if self.server.sse else "application/json"
            )
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.categories = ["ask-docs-26.10", "user-docs-26.10"]
    server.results = [
        {"name": "viraStack.pdf", "score": 0.72, "link": "cda://doc/viraStack/viraStack.pdf",
         "page_content": "ViraStack 环境变量设置与网关地址说明。"},
        {"name": "setup.md", "score": 0.81,
         "link": "https://support.example/solution/1?id=setup",
         "page_content": "JedAI setup guide: configure the embedding service first."},
    ]
    server.seen, server.delay, server.sse = [], 0, False
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server, jedai("http://127.0.0.1:" + str(server.server_port) + "/api/aai/mcp/")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
