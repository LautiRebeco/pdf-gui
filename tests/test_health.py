"""Endpoint opcional de health (factor VII)."""

from __future__ import annotations

import json
import socket
import urllib.request
from dataclasses import replace

from app_settings import load_settings
from health import HealthServer


def puerto_libre():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_health_server_responde_y_se_apaga():
    settings = replace(
        load_settings({"PDF_EXTRACT_GATEWAY_URL": "https://gateway.local"}),
        health_port=puerto_libre(),
    )
    servidor = HealthServer(settings)
    assert servidor.enabled
    servidor.start()
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{settings.health_port}/health", timeout=2
        ) as r:
            datos = json.load(r)
        assert datos["status"] == "ok"
        assert datos["gateway"] == "https://gateway.local"
    finally:
        servidor.stop()
    assert servidor._server is None


def test_health_server_desactivado_por_defecto():
    settings = load_settings({"PDF_EXTRACT_GATEWAY_URL": "https://gateway.local"})
    servidor = HealthServer(settings)
    assert not servidor.enabled
    servidor.start()
    assert servidor._server is None
