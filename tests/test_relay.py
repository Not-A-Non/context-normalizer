from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from context_normalizer import workspace as ws
from context_normalizer.cli import main as cli_main
from context_normalizer.config import Rule
from context_normalizer.relay import (
    StreamDisplayTranslator,
    create_relay_server,
    rewrite_request_document,
    rewrite_response_document,
    to_display_text,
)


PATH_RULES = [Rule("kernel", "runtime boundary")]
RULES = [Rule("repro steps", "reproduction steps")]


class RewriteDocumentTests(unittest.TestCase):
    def test_request_rewrites_model_bound_text_only(self) -> None:
        document = {
            "model": "model-id",
            "system": "kernel notes",
            "messages": [
                {"role": "user", "content": "Sync repro steps for kernel data"},
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "kernel stays untouched",
                            "signature": "sig",
                        },
                        {"type": "text", "text": "kernel report"},
                        {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "reader",
                            "input": {"path": "kernel.txt"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tool-1",
                            "content": [{"type": "text", "text": "kernel data"}],
                        }
                    ],
                },
            ],
        }
        result = rewrite_request_document(document, RULES, PATH_RULES)
        self.assertEqual("runtime boundary notes", result["system"])
        self.assertEqual(
            "Sync reproduction steps for runtime boundary data",
            result["messages"][0]["content"],
        )
        assistant = result["messages"][1]["content"]
        self.assertEqual("kernel stays untouched", assistant[0]["thinking"])
        self.assertEqual("runtime boundary report", assistant[1]["text"])
        self.assertEqual({"path": "kernel.txt"}, assistant[2]["input"])
        self.assertEqual(
            "runtime boundary data",
            result["messages"][2]["content"][0]["content"][0]["text"],
        )
        # The original document is never mutated.
        self.assertEqual("kernel notes", document["system"])

    def test_response_translates_text_blocks_for_display(self) -> None:
        document = {
            "content": [
                {"type": "text", "text": "runtime boundary data ready"},
                {
                    "type": "thinking",
                    "thinking": "runtime boundary hidden",
                    "signature": "sig",
                },
            ]
        }
        result = rewrite_response_document(document, PATH_RULES)
        self.assertEqual("kernel data ready", result["content"][0]["text"])
        self.assertEqual("runtime boundary hidden", result["content"][1]["thinking"])


def _sse(events: list[tuple[str, dict[str, object]]]) -> bytes:
    parts = []
    for name, document in events:
        parts.append(f"event: {name}\ndata: {json.dumps(document)}\n\n")
    return "".join(parts).encode("utf-8")


class StreamDisplayTranslatorTests(unittest.TestCase):
    def _translate(self, text: str) -> str:
        return to_display_text(text, PATH_RULES)

    def test_text_deltas_coalesce_and_translate_at_block_stop(self) -> None:
        stream = _sse(
            [
                ("message_start", {"type": "message_start", "message": {}}),
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "runtime bou"},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "ndary data ready"},
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_stop", {"type": "message_stop"}),
            ]
        )
        translator = StreamDisplayTranslator(self._translate)
        output = bytearray()
        # Feed one byte at a time so events arrive divided at every boundary.
        for offset in range(len(stream)):
            output += translator.push(stream[offset : offset + 1])
        output += translator.finish()
        text = output.decode("utf-8")
        self.assertIn("kernel data ready", text)
        self.assertNotIn("runtime boundary", text)
        self.assertLess(text.index("message_start"), text.index("kernel data ready"))
        self.assertLess(
            text.index("kernel data ready"), text.index("content_block_stop")
        )
        self.assertLess(text.index("content_block_stop"), text.index("message_stop"))

    def test_non_text_blocks_pass_through_unchanged(self) -> None:
        stream = _sse(
            [
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "tool_use",
                            "id": "tool-1",
                            "name": "runner",
                            "input": {},
                        },
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {
                            "type": "input_json_delta",
                            "partial_json": '{"path": "runtime boundary"}',
                        },
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("ping", {"type": "ping"}),
            ]
        )
        translator = StreamDisplayTranslator(self._translate)
        output = translator.push(stream) + translator.finish()
        self.assertEqual(stream, output)

    def test_prefilled_text_on_block_start_is_buffered(self) -> None:
        stream = _sse(
            [
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {
                            "type": "text",
                            "text": "runtime boundary head",
                        },
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
            ]
        )
        translator = StreamDisplayTranslator(self._translate)
        text = (translator.push(stream) + translator.finish()).decode("utf-8")
        self.assertIn("kernel head", text)
        self.assertNotIn("runtime boundary head", text)


class _UpstreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self.server.requests.append(
            {
                "path": self.path,
                "body": body,
                "headers": {
                    name.lower(): value for name, value in self.headers.items()
                },
            }
        )
        payload = self.server.reply
        self.send_response(200)
        self.send_header("Content-Type", self.server.reply_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class RelayEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        home = self.root / "normalizer"
        home.mkdir()
        home.joinpath("rules.tsv").write_text(
            "repro steps\treproduction steps\n", encoding="utf-8"
        )
        home.joinpath("path-rules.tsv").write_text(
            "kernel\truntime boundary\n", encoding="utf-8"
        )
        for name in ("cues.txt", "context.txt", "subagent-context.txt"):
            home.joinpath(name).write_text("", encoding="utf-8")
        environment = patch.dict(
            os.environ, {"CONTEXT_NORMALIZER_HOME": str(home)}, clear=False
        )
        environment.start()
        self.addCleanup(environment.stop)

    def _start_upstream(self, reply: bytes, reply_type: str) -> str:
        upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
        upstream.requests = []
        upstream.reply = reply
        upstream.reply_type = reply_type
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)
        self.upstream = upstream
        return f"http://127.0.0.1:{upstream.server_address[1]}"

    def _start_relay(self, upstream: str, mirror: Path | None = None) -> str:
        relay = create_relay_server(upstream=upstream, mirror=mirror)
        threading.Thread(target=relay.serve_forever, daemon=True).start()
        self.addCleanup(relay.server_close)
        self.addCleanup(relay.shutdown)
        return f"http://127.0.0.1:{relay.server_address[1]}"

    def _post(self, address: str, document: dict[str, object]) -> bytes:
        request = urllib.request.Request(
            f"{address}/v1/messages",
            data=json.dumps(document).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-api-key": "test-key"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()

    def test_json_round_trip_normalizes_out_and_translates_back(self) -> None:
        reply = json.dumps(
            {
                "content": [
                    {"type": "text", "text": "runtime boundary data ready"},
                ]
            }
        ).encode("utf-8")
        upstream = self._start_upstream(reply, "application/json")
        relay = self._start_relay(upstream)
        body = self._post(
            relay,
            {
                "model": "model-id",
                "messages": [
                    {"role": "user", "content": "Sync repro steps for kernel data"}
                ],
            },
        )
        sent = json.loads(self.upstream.requests[0]["body"].decode("utf-8"))
        self.assertEqual(
            "Sync reproduction steps for runtime boundary data",
            sent["messages"][0]["content"],
        )
        self.assertEqual("test-key", self.upstream.requests[0]["headers"]["x-api-key"])
        self.assertEqual(
            "identity", self.upstream.requests[0]["headers"]["accept-encoding"]
        )
        received = json.loads(body.decode("utf-8"))
        self.assertEqual("kernel data ready", received["content"][0]["text"])

    def test_stream_round_trip_translates_text_events(self) -> None:
        reply = _sse(
            [
                ("message_start", {"type": "message_start", "message": {}}),
                (
                    "content_block_start",
                    {
                        "type": "content_block_start",
                        "index": 0,
                        "content_block": {"type": "text", "text": ""},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": "runtime boundary"},
                    },
                ),
                (
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": 0,
                        "delta": {"type": "text_delta", "text": " data ready"},
                    },
                ),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_stop", {"type": "message_stop"}),
            ]
        )
        upstream = self._start_upstream(reply, "text/event-stream")
        relay = self._start_relay(upstream)
        body = self._post(
            relay,
            {
                "model": "model-id",
                "stream": True,
                "messages": [{"role": "user", "content": "kernel data"}],
            },
        ).decode("utf-8")
        self.assertIn("kernel data ready", body)
        self.assertNotIn("runtime boundary", body)
        self.assertIn("message_stop", body)

    def test_invalid_body_fails_closed_without_forwarding(self) -> None:
        upstream = self._start_upstream(b"{}", "application/json")
        relay = self._start_relay(upstream)
        request = urllib.request.Request(
            f"{relay}/v1/messages",
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as context:
            urllib.request.urlopen(request, timeout=30)
        self.assertEqual(400, context.exception.code)
        document = json.loads(context.exception.read().decode("utf-8"))
        self.assertEqual("error", document["type"])
        self.assertEqual([], self.upstream.requests)

    def test_workspace_synchronizes_around_each_turn(self) -> None:
        source = self.root / "source"
        managed = self.root / "managed"
        source.mkdir()
        managed.mkdir()
        (source / "notes.txt").write_text("kernel data\n", encoding="utf-8")
        created = ws.create_workspace(
            source,
            managed,
            mode="filesystem",
            path_rules=[Rule("kernel", "runtime boundary")],
        )
        mirror = Path(created["mirror"])
        upstream = self._start_upstream(
            json.dumps({"content": []}).encode("utf-8"), "application/json"
        )
        relay = self._start_relay(upstream, mirror=mirror)
        # A source edit flows into the mirror before the request is forwarded.
        (source / "notes.txt").write_text("kernel data two\n", encoding="utf-8")
        self._post(relay, {"model": "model-id", "messages": []})
        self.assertEqual(
            "runtime boundary data two\n",
            (mirror / "notes.txt").read_text(encoding="utf-8"),
        )
        # A completed mirror edit flows back to the source on the next turn.
        (mirror / "notes.txt").write_text(
            "runtime boundary data three\n", encoding="utf-8"
        )
        self._post(relay, {"model": "model-id", "messages": []})
        self.assertEqual(
            "kernel data three\n",
            (source / "notes.txt").read_text(encoding="utf-8"),
        )


class RelayRunTests(unittest.TestCase):
    def test_run_exports_base_address_and_returns_child_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "normalizer"
            home.mkdir()
            capture = Path(directory) / "address.txt"
            code = (
                "import os, pathlib; "
                f"pathlib.Path({str(capture)!r}).write_text("
                "os.environ['ANTHROPIC_BASE_URL'], encoding='utf-8')"
            )
            with patch.dict(
                os.environ, {"CONTEXT_NORMALIZER_HOME": str(home)}, clear=False
            ):
                status = cli_main(["relay", "run", "--", sys.executable, "-c", code])
            self.assertEqual(0, status)
            self.assertTrue(
                capture.read_text(encoding="utf-8").startswith("http://127.0.0.1:")
            )


if __name__ == "__main__":
    unittest.main()
