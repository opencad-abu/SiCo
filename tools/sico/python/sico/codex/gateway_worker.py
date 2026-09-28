"""One remote HTTP exchange; credentials arrive privately after LSF starts the worker."""

import http.client
import json
import os
import select
import socket
import sys
import threading
from urllib.parse import urlsplit

from cadai.provider_settings import provider_endpoint, validate_key
from cadai.response_http_error import body as error_body, rejection
from .gateway_wire import Frames, MAX_BODY, chunk, decode_data, encode, send


def serve():
    # bsub -Is allocates a PTY. Disable echo and terminal line discipline before
    # announcing readiness; binary content is transported as bounded ASCII frames.
    if os.isatty(0):
        import tty

        tty.setraw(0)
    source, output = sys.stdin.buffer, sys.stdout.buffer
    reader = Frames(source)
    send(output, encode("ready"))
    config = reader.next(30)
    if config.get("kind") != "request":
        raise ValueError("Missing gateway request")
    url = urlsplit(provider_endpoint(config["endpoint"], "responses"))
    validate_key(config["key"])
    timeout, idle = config["timeout"], config["idle"]
    if not 0 < timeout <= 120 or not 0 < idle <= 3600:
        raise ValueError("Invalid gateway timeout")
    body = bytearray()
    while True:
        row = reader.next(30)
        if row.get("kind") == "end":
            break
        if row.get("kind") != "data":
            raise ValueError("Invalid gateway request body")
        body.extend(decode_data(row))
        if len(body) > MAX_BODY:
            raise ValueError("Gateway request exceeds limit")
    connection = (
        http.client.HTTPSConnection
        if url.scheme == "https"
        else http.client.HTTPConnection
    )(url.hostname, url.port, timeout=timeout)
    done = threading.Event()
    sockets = []

    def cancel():
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def watch():
        while not done.wait(0.1):
            if select.select([source], [], [], 0)[0]:
                os.read(0, 1)  # Any control byte or EOF means caller disappeared.
                done.set()
                cancel()
                return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        connection.connect()
        sockets.append(connection.sock)
        connection.sock.settimeout(idle)
        if done.is_set():
            return
        connection.request(
            "POST",
            url.path,
            body=body,
            headers={
                "Authorization": "Bearer " + config["key"],
                "Content-Type": "application/json",
                "Accept": "text/event-stream, application/json",
                "Accept-Encoding": "identity",
            },
        )
        response = connection.getresponse()
        content = response.getheader("Content-Type", "").split(";")[0].strip()
        status = response.status
        if status != 200:
            details = rejection(response, (config["key"],))
            send(output, encode("headers", status=status, content="application/json",
                                request_id=details.get("request_id", "")))
            send(output, chunk(json.dumps(error_body(status, details)).encode()))
        else:
            if (
                content not in {"application/json", "text/event-stream"}
                or response.getheader("Content-Encoding", "identity") != "identity"
            ):
                raise ValueError("Unsupported upstream response")
            send(output, encode("headers", status=200, content=content))
            count = 0
            while not done.is_set():
                data = response.read1(4096)
                if not data:
                    break
                count += len(data)
                if count > 128 * 1024 * 1024:
                    raise ValueError("Upstream response exceeds limit")
                send(output, chunk(data))
        send(output, encode("end"))
    except (OSError, ValueError, http.client.HTTPException):
        send(
            output,
            encode("error", message="AI queue worker could not complete upstream HTTP"),
        )
    finally:
        done.set()
        cancel()
        connection.close()
        watcher.join(timeout=1)


def main():
    try:
        serve()
        return 0
    except (OSError, ValueError, EOFError, KeyError, TypeError):
        return 2  # No untrusted exception body or configuration in stderr.
