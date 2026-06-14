#!/usr/bin/env python3
"""Small OpenAI-compatible fake backend for local router smoke tests."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import argparse
import json
import time
from typing import Any


class FakeLlamaHandler(BaseHTTPRequestHandler):
    backend_name = "fake"
    delay_ms = 0.0
    tokens_out = 8

    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        payload = {"status": "ok", "model": self.backend_name}
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("content-length", "0"))
        self.rfile.read(length)
        if self.delay_ms > 0:
            time.sleep(self.delay_ms / 1000.0)
        text = f"response from {self.backend_name}"
        payload = {
            "id": f"fake-{self.backend_name}-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": self.backend_name,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": self.tokens_out,
                "total_tokens": self.tokens_out,
            },
        }
        body = json.dumps(payload).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--name", default="fake")
    parser.add_argument("--delay-ms", type=float, default=0.0)
    parser.add_argument("--tokens-out", type=int, default=8)
    args = parser.parse_args()

    class Handler(FakeLlamaHandler):
        pass

    Handler.backend_name = args.name
    Handler.delay_ms = args.delay_ms
    Handler.tokens_out = args.tokens_out
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"fake llama server {args.name} on http://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
