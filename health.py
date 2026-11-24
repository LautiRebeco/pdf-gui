"""Endpoint opcional de supervisión del proceso (factor VII).

El cliente de escritorio no expone servicios de negocio, pero puede publicar
un puerto de administración local para que el orquestador (o un supervisor)
compruebe que el proceso sigue vivo. Se habilita con
``PDF_EXTRACT_HEALTH_PORT``; por defecto está desactivado y solo escucha en
loopback.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app_settings import Settings

logger = logging.getLogger(__name__)


class HealthServer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.health_port > 0

    def start(self) -> None:
        if not self.enabled or self._server is not None:
            return

        settings = self.settings
        app = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "PDFExtractGUI/1.0"

            def do_GET(self) -> None:  # noqa: N802 - API de BaseHTTPRequestHandler
                if self.path.split("?", 1)[0] in {"/health", "/ready"}:
                    payload = {
                        "status": "ok",
                        "version": app.settings.version,
                        "gateway": app.settings.gateway_url,
                    }
                    self._send(200, payload)
                else:
                    self._send(404, {"detail": "Not Found"})

            def _send(self, status: int, payload: dict[str, object]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, fmt: str, *args: object) -> None:
                logger.debug("health.request %s", fmt % args)

        host = settings.health_host or "127.0.0.1"
        self._server = ThreadingHTTPServer((host, settings.health_port), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="pdf-gui-health",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "health.started",
            extra={"event": "health.started", "address": f"{host}:{settings.health_port}"},
        )

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None
        logger.info("health.stopped", extra={"event": "health.stopped"})
