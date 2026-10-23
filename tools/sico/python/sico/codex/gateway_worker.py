"""Resident LSF worker; credentials arrive privately after the job starts."""

import http.client
import json
import os
import socket
import sys
import threading
from urllib.parse import urlsplit

from cadai.provider_settings import provider_endpoint, validate_key
from cadai.response_http_error import body as error_body, rejection
from ..configuration.codex import codex_idle_timeout
from .gateway_wire import Frames, MAX_BODY, chunk, decode_data, encode, send

IDLE_POLL = 3600


def _request(config):
    url = urlsplit(provider_endpoint(config["endpoint"], "responses"))
    validate_key(config["key"])
    timeout, idle = config["timeout"], config["idle"]
    if type(timeout) not in {int, float} or not 0 < timeout <= 120:
        raise ValueError("Invalid gateway timeout")
    idle = codex_idle_timeout({}, idle)
    return url, config["key"], timeout, idle


def _read_body(reader):
    body = bytearray()
    too_large = False
    while True:
        row = reader.next(30)
        if row.get("kind") == "end":
            if too_large:
                raise ValueError("Gateway request exceeds limit")
            return bytes(body)
        if row.get("kind") != "data":
            raise ValueError("Invalid gateway request body")
        if not too_large:
            body.extend(decode_data(row))
            too_large = len(body) > MAX_BODY


def _exchange(config, body, reader, output):
    """Serve one request; return True when the gateway requested shutdown."""
    url, key, timeout, idle = _request(config)
    connection = (
        http.client.HTTPSConnection
        if url.scheme == "https"
        else http.client.HTTPConnection
    )(url.hostname, url.port, timeout=timeout)
    done = threading.Event()
    cancel_event = threading.Event()
    shutdown_event = threading.Event()
    sockets = []

    def abort():
        for sock in sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def watch():
        while not done.wait(0.1):
            try:
                row = reader.next(0.2, lambda: done.is_set())
            except TimeoutError:
                continue
            except (EOFError, OSError):
                if done.is_set():
                    return
                cancel_event.set()
                shutdown_event.set()
                abort()
                return
            kind = row.get("kind")
            if kind == "cancel":
                cancel_event.set()
                abort()
            elif kind == "shutdown":
                cancel_event.set()
                shutdown_event.set()
                abort()
                return
            else:
                cancel_event.set()
                shutdown_event.set()
                abort()
                return

    watcher = threading.Thread(target=watch, daemon=True)
    watcher.start()
    try:
        connection.connect()
        sockets.append(connection.sock)
        connection.sock.settimeout(idle)
        if cancel_event.is_set():
            send(output, encode("end"))
            return shutdown_event.is_set()
        connection.request(
            "POST",
            url.path,
            body=body,
            headers={
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream, application/json",
                "Accept-Encoding": "identity",
            },
        )
        response = connection.getresponse()
        content = response.getheader("Content-Type", "").split(";")[0].strip()
        status = response.status
        if status != 200:
            details = rejection(response, (key,))
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
            while not cancel_event.is_set():
                data = response.read1(4096)
                if not data:
                    break
                count += len(data)
                if count > 128 * 1024 * 1024:
                    raise ValueError("Upstream response exceeds limit")
                send(output, chunk(data))
        send(output, encode("end"))
    except (OSError, ValueError, http.client.HTTPException):
        if cancel_event.is_set():
            send(output, encode("end"))
        else:
            send(
                output,
                encode("error", message="AI queue worker could not complete upstream HTTP"),
            )
            send(output, encode("end"))
    finally:
        done.set()
        abort()
        connection.close()
        watcher.join(timeout=1)
        if watcher.is_alive():
            shutdown_event.set()
    return shutdown_event.is_set()


def serve():
    # bsub -Is allocates a PTY. Disable echo and terminal line discipline before
    # announcing readiness; binary content is transported as bounded ASCII frames.
    if os.isatty(0):
        import tty

        tty.setraw(0)
    source, output = sys.stdin.buffer, sys.stdout.buffer
    try:
        from sicoruntime import validate as validate_runtime

        validate_runtime(os.environ)
    except RuntimeError as exc:
        send(output, encode("error", message=str(exc)))
        return
    reader = Frames(source)
    send(output, encode("ready"))
    while True:
        try:
            config = reader.next(IDLE_POLL)
        except TimeoutError:
            continue
        except EOFError:
            return
        kind = config.get("kind")
        if kind == "shutdown":
            return
        if kind == "cancel":
            continue
        if kind != "request":
            send(output, encode("error", message="Invalid gateway control frame"))
            return
        try:
            body = _read_body(reader)
        except EOFError:
            return
        except ValueError:
            send(output, encode("error", message="Invalid gateway request body"))
            send(output, encode("end"))
            return
        if _exchange(config, body, reader, output):
            return


def main():
    try:
        serve()
        return 0
    except (OSError, ValueError, EOFError, KeyError, TypeError):
        return 2  # No untrusted exception body or configuration in stderr.
