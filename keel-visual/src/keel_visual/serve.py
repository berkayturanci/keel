"""A tiny localhost HTTP server for the **live** web dashboard.

`render --all` writes a static snapshot — a run that starts afterwards never
appears. `serve` instead keeps the page open and live: it serves the dashboard
HTML once and a `/board.json` endpoint that **re-reads the records on every
request**, so the page can poll and update itself.

Pure-core + thin I/O: :func:`route` is a pure ``(status, content_type, body)``
router (fully testable); the handler and server are thin wrappers. Binds to
``127.0.0.1`` only — the board data never leaves the machine.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

BoardProvider = Callable[[], list[dict[str, Any]]]


def route(
    path: str, *, board_provider: BoardProvider, page_html: str
) -> tuple[int, str, bytes]:
    """Resolve a GET ``path`` to ``(status, content_type, body)``. Pure.

    ``/`` (and ``/index.html``) serve the dashboard page; ``/board.json`` calls
    ``board_provider`` fresh each time (the live re-read); anything else is 404.
    """
    clean = path.split("?", 1)[0]
    if clean in ("/", "/index.html"):
        return 200, "text/html; charset=utf-8", page_html.encode("utf-8")
    if clean == "/board.json":
        body = json.dumps(board_provider(), sort_keys=True).encode("utf-8")
        return 200, "application/json; charset=utf-8", body
    return 404, "text/plain; charset=utf-8", b"not found"


def make_handler(board_provider: BoardProvider, page_html: str) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to ``board_provider`` + ``page_html``."""

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib API name
            status, content_type, body = route(
                self.path, board_provider=board_provider, page_html=page_html)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: Any) -> None:  # silence per-request logging
            pass

    return _Handler


def make_server(
    board_provider: BoardProvider,
    page_html: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    server_factory: Callable[..., ThreadingHTTPServer] = ThreadingHTTPServer,
):
    """Create (but do not start) the dashboard server. Localhost-only by default."""
    return server_factory((host, port), make_handler(board_provider, page_html))
