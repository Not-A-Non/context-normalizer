"""Local API relay that normalizes model-bound traffic at the network boundary.

Any client that honors a configurable API base address (for example the
`ANTHROPIC_BASE_URL` environment variable) can point at the relay. Outbound
message bodies are normalized before they leave the machine, and inbound
assistant text is translated back for display. No client source changes are
required.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import threading
from http.client import HTTPConnection, HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .config import Rule, load_path_rules, load_rules
from .normalize import normalize_text, translate_reversible_text
from .workspace import WorkspaceError


DEFAULT_UPSTREAM = "https://api.anthropic.com"
# Exact request paths whose JSON bodies carry model-bound conversation text.
# Batch submissions are intentionally excluded and pass through unchanged.
TEXT_BEARING_PATHS = ("/v1/messages", "/v1/messages/count_tokens")
# Signed blocks must round-trip byte-identical or the upstream rejects them,
# so they are never rewritten in either direction.
SIGNED_BLOCK_TYPES = ("thinking", "redacted_thinking")
_SKIPPED_REQUEST_HEADERS = (
    "accept-encoding",
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "te",
    "transfer-encoding",
    "upgrade",
)
_SKIPPED_RESPONSE_HEADERS = (
    "connection",
    "content-encoding",
    "content-length",
    "keep-alive",
    "transfer-encoding",
)


def to_model_text(text: str, rules: list[Rule], path_rules: list[Rule]) -> str:
    translated = translate_reversible_text(text, path_rules)
    normalized, _ = normalize_text(translated, rules, context=None)
    return normalized


def to_display_text(text: str, path_rules: list[Rule]) -> str:
    return translate_reversible_text(text, path_rules, reverse=True)


def _rewrite_block_to_model(
    block: dict[str, object], rewrite: Callable[[str], str]
) -> None:
    kind = block.get("type")
    if kind in SIGNED_BLOCK_TYPES:
        return
    if kind == "text" and isinstance(block.get("text"), str):
        block["text"] = rewrite(block["text"])
        return
    if kind == "tool_result":
        content = block.get("content")
        if isinstance(content, str):
            block["content"] = rewrite(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    _rewrite_block_to_model(item, rewrite)
        return
    if kind == "document":
        source = block.get("source")
        if isinstance(source, dict) and source.get("type") == "text":
            data = source.get("data")
            if isinstance(data, str):
                source["data"] = rewrite(data)


def rewrite_request_document(
    document: dict[str, object], rules: list[Rule], path_rules: list[Rule]
) -> dict[str, object]:
    """Normalize every model-bound text field of a messages request body."""

    result = copy.deepcopy(document)

    def rewrite(text: str) -> str:
        return to_model_text(text, rules, path_rules)

    system = result.get("system")
    if isinstance(system, str):
        result["system"] = rewrite(system)
    elif isinstance(system, list):
        for block in system:
            if isinstance(block, dict):
                _rewrite_block_to_model(block, rewrite)
    messages = result.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if isinstance(content, str):
                message["content"] = rewrite(content)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, dict):
                        _rewrite_block_to_model(block, rewrite)
    return result


def rewrite_response_document(
    document: dict[str, object], path_rules: list[Rule]
) -> dict[str, object]:
    """Translate assistant text blocks of a completed response for display."""

    result = copy.deepcopy(document)
    content = result.get("content")
    if isinstance(content, list):
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                block["text"] = to_display_text(block["text"], path_rules)
    return result


class StreamDisplayTranslator:
    """Translate assistant text inside a server-sent event stream.

    A partial delta could divide a vocabulary match, so each text block is
    accumulated and emitted as a single delta when its block completes. All
    other events pass through unchanged in their original order.
    """

    def __init__(self, translate: Callable[[str], str]) -> None:
        self._translate = translate
        self._pending = b""
        self._text_blocks: dict[int, list[str]] = {}

    def push(self, data: bytes) -> bytes:
        self._pending += data
        output = bytearray()
        while True:
            boundary = self._pending.find(b"\n\n")
            if boundary < 0:
                break
            event = self._pending[:boundary]
            self._pending = self._pending[boundary + 2 :]
            output += self._handle_event(event)
        return bytes(output)

    def finish(self) -> bytes:
        remainder = self._pending
        self._pending = b""
        return remainder

    def _handle_event(self, raw: bytes) -> bytes:
        document = _event_document(raw)
        if document is None:
            return raw + b"\n\n"
        kind = document.get("type")
        index = document.get("index")
        if kind == "content_block_start":
            block = document.get("content_block")
            if isinstance(block, dict) and block.get("type") == "text":
                self._text_blocks[index] = []
                if isinstance(block.get("text"), str) and block["text"]:
                    self._text_blocks[index].append(block["text"])
                    block["text"] = ""
                    return _serialize_event("content_block_start", document)
            return raw + b"\n\n"
        if kind == "content_block_delta" and index in self._text_blocks:
            delta = document.get("delta")
            if isinstance(delta, dict) and delta.get("type") == "text_delta":
                text = delta.get("text")
                if isinstance(text, str):
                    self._text_blocks[index].append(text)
                    return b""
            return raw + b"\n\n"
        if kind == "content_block_stop" and index in self._text_blocks:
            text = "".join(self._text_blocks.pop(index))
            output = bytearray()
            if text:
                output += _serialize_event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {
                            "type": "text_delta",
                            "text": self._translate(text),
                        },
                    },
                )
            output += raw + b"\n\n"
            return bytes(output)
        return raw + b"\n\n"


def _event_document(raw: bytes) -> dict[str, object] | None:
    for line in raw.split(b"\n"):
        if line.startswith(b"data:"):
            try:
                document = json.loads(line[5:].strip().decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                return None
            return document if isinstance(document, dict) else None
    return None


def _serialize_event(name: str, document: dict[str, object]) -> bytes:
    payload = json.dumps(document, separators=(",", ":"))
    return f"event: {name}\ndata: {payload}\n\n".encode("utf-8")


class RelayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        upstream: str,
        mirror: Path | None,
    ) -> None:
        super().__init__(address, RelayHandler)
        parts = urlsplit(upstream)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"invalid upstream address: {upstream}")
        self.upstream = upstream
        self.upstream_parts = parts
        self.mirror = mirror
        self._sync_lock = threading.Lock()

    def synchronize_workspace(self) -> None:
        # Complete mirror changes flow back to the source first, then source
        # edits flow forward, so each turn starts from a converged workspace.
        # Conflicts fail closed: the request is refused, never forwarded raw.
        if self.mirror is None:
            return
        from .feature_cli import _auto_sync, _prepare_workspace

        from .workspace import status_workspace

        with self._sync_lock:
            status = status_workspace(self.mirror)
            if status["clean"]:
                return
            if status["mirror_changed"]:
                outcome = _auto_sync(self.mirror)
                if outcome["status"] == "conflict":
                    raise WorkspaceError(
                        "workspace conflicts paused source normalization"
                    )
            if status["source_changed"]:
                _prepare_workspace(self.mirror)


class RelayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server: RelayServer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionResetError, BrokenPipeError, TimeoutError):
            self.close_connection = True

    def do_GET(self) -> None:
        self._forward("GET")

    def do_POST(self) -> None:
        self._forward("POST")

    def do_DELETE(self) -> None:
        self._forward("DELETE")

    def _forward(self, method: str) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        path = urlsplit(self.path).path
        text_bearing = method == "POST" and path in TEXT_BEARING_PATHS
        if text_bearing:
            try:
                if path == "/v1/messages":
                    self.server.synchronize_workspace()
                document = json.loads(body.decode("utf-8"))
                path_rules = load_path_rules()
                body = json.dumps(
                    rewrite_request_document(document, load_rules(), path_rules)
                ).encode("utf-8")
            except (ValueError, WorkspaceError, OSError) as exc:
                self._send_json(
                    400,
                    {
                        "type": "error",
                        "error": {
                            "type": "invalid_request_error",
                            "message": f"context normalization refused the request: {exc}",
                        },
                    },
                )
                return
        try:
            connection = self._open_upstream()
            connection.request(
                method, self._upstream_target(), body=body, headers=self._headers()
            )
            response = connection.getresponse()
        except OSError as exc:
            self._send_json(
                502,
                {
                    "type": "error",
                    "error": {
                        "type": "api_error",
                        "message": f"upstream request failed: {exc}",
                    },
                },
            )
            return
        try:
            content_type = response.getheader("Content-Type") or ""
            if text_bearing and content_type.startswith("text/event-stream"):
                self._stream_translated(response, path_rules)
            else:
                data = response.read()
                if (
                    text_bearing
                    and response.status == 200
                    and content_type.startswith("application/json")
                    and path == "/v1/messages"
                ):
                    data = json.dumps(
                        rewrite_response_document(
                            json.loads(data.decode("utf-8")), path_rules
                        )
                    ).encode("utf-8")
                self._send_upstream(response, data)
        except OSError:
            self.close_connection = True
        finally:
            connection.close()

    def _open_upstream(self) -> HTTPConnection:
        parts = self.server.upstream_parts
        if parts.scheme == "https":
            return HTTPSConnection(parts.hostname, parts.port, timeout=600)
        return HTTPConnection(parts.hostname, parts.port, timeout=600)

    def _upstream_target(self) -> str:
        prefix = self.server.upstream_parts.path.rstrip("/")
        return f"{prefix}{self.path}" if prefix else self.path

    def _headers(self) -> dict[str, str]:
        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in _SKIPPED_REQUEST_HEADERS
        }
        headers["Accept-Encoding"] = "identity"
        headers["Connection"] = "close"
        return headers

    def _send_json(self, status: int, document: dict[str, object]) -> None:
        payload = json.dumps(document).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _send_upstream(self, response: object, data: bytes) -> None:
        self.send_response(response.status)
        for name, value in response.getheaders():
            if name.lower() not in _SKIPPED_RESPONSE_HEADERS:
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)

    def _stream_translated(self, response: object, path_rules: list[Rule]) -> None:
        self.send_response(response.status)
        for name, value in response.getheaders():
            if name.lower() not in _SKIPPED_RESPONSE_HEADERS:
                self.send_header(name, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        translator = StreamDisplayTranslator(
            lambda text: to_display_text(text, path_rules)
        )
        while True:
            chunk = (
                response.read1(65536)
                if hasattr(response, "read1")
                else response.read(65536)
            )
            if not chunk:
                break
            self._write_chunk(translator.push(chunk))
        self._write_chunk(translator.finish())
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def _write_chunk(self, data: bytes) -> None:
        if data:
            self.wfile.write(f"{len(data):X}\r\n".encode("ascii") + data + b"\r\n")
            self.wfile.flush()


def create_relay_server(
    *,
    port: int = 0,
    upstream: str = DEFAULT_UPSTREAM,
    mirror: Path | None = None,
) -> RelayServer:
    return RelayServer(("127.0.0.1", port), upstream=upstream, mirror=mirror)


def _resolve_mirror(value: str | None) -> Path | None:
    if not value:
        return None
    from .feature_cli import _mirror

    return _mirror(value)


def _relay_serve(args: argparse.Namespace) -> int:
    server = create_relay_server(
        port=args.port,
        upstream=args.upstream,
        mirror=_resolve_mirror(args.workspace),
    )
    print(
        json.dumps(
            {
                "address": f"http://127.0.0.1:{server.server_address[1]}",
                "upstream": server.upstream,
                "workspace": str(server.mirror) if server.mirror else None,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def _relay_run(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ValueError("relay run requires a client command after --")
    server = create_relay_server(
        port=args.port,
        upstream=args.upstream,
        mirror=_resolve_mirror(args.workspace),
    )
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    address = f"http://127.0.0.1:{server.server_address[1]}"
    environment = dict(os.environ)
    environment["ANTHROPIC_BASE_URL"] = address
    resolved = shutil.which(command[0]) or command[0]
    try:
        completed = subprocess.run(
            [resolved, *command[1:]],
            env=environment,
            cwd=str(server.mirror) if server.mirror else None,
        )
        return completed.returncode
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)


def add_relay_commands(
    commands: argparse._SubParsersAction[argparse.ArgumentParser],
) -> None:
    relay = commands.add_parser("relay", help="serve the local API normalization relay")
    relay_commands = relay.add_subparsers(dest="relay_command", required=True)
    serve = relay_commands.add_parser("serve", help="serve the relay on a local port")
    serve.add_argument("--port", type=int, default=8377)
    serve.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    serve.add_argument("--workspace", help="normalized workspace path or identifier")
    serve.set_defaults(function=_relay_serve)
    run = relay_commands.add_parser(
        "run", help="run a client command against an ephemeral relay"
    )
    run.add_argument("--port", type=int, default=0)
    run.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    run.add_argument("--workspace", help="normalized workspace path or identifier")
    run.add_argument("command", nargs=argparse.REMAINDER)
    run.set_defaults(function=_relay_run)
