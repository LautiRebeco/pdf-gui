"""Logs correlacionados y Processes administrativos."""

from __future__ import annotations

import logging

from app_logging import (
    RequestIdFilter,
    configure_logging,
    current_request_id,
    ensure_request_context,
    request_context,
)
from app_settings import load_settings


def test_request_context_define_y_limpia_el_id():
    assert current_request_id() == "-"
    with request_context("abc123") as request_id:
        assert request_id == "abc123"
        assert current_request_id() == "abc123"
    assert current_request_id() == "-"


def test_ensure_request_context_reutiliza_el_activo():
    with request_context("outer") as outer:
        with ensure_request_context("ignored") as inner:
            assert inner == outer == "outer"
            assert current_request_id() == "outer"


def test_ensure_request_context_crea_uno_si_no_hay():
    with ensure_request_context("generado") as request_id:
        assert request_id == "generado"
        assert current_request_id() == "generado"


def test_filter_anade_request_id_y_evento():
    filtro = RequestIdFilter()
    registro = logging.LogRecord("x", logging.INFO, __file__, 1, "mensaje", (), None)
    with request_context("id-42"):
        assert filtro.filter(registro) is True
    assert registro.request_id == "id-42"
    assert registro.event == "app.event"


def test_configure_logging_crea_archivo_opcional(tmp_path):
    archivo = tmp_path / "logs" / "app.log"
    settings = load_settings(
        {
            "PDF_EXTRACT_GATEWAY_URL": "https://gateway.local",
            "PDF_EXTRACT_LOG_FILE": str(archivo),
        }
    )
    root = configure_logging(settings)
    root.info("mensaje de prueba")
    for handler in root.handlers:
        handler.flush()
    assert archivo.is_file()
    assert "mensaje de prueba" in archivo.read_text(encoding="utf-8")
