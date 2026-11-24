"""Logs estructurados como flujo de eventos (factor XI).

Cada petición HTTP lleva un ``X-Request-ID``; el mismo identificador se
propaga a los logs del cliente para poder correlacionarlos con los del
servicio de documentos y con Traefik.
"""

from __future__ import annotations

import contextvars
import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app_settings import Settings

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "request_id", default=None
)

_LOG_FORMAT = (
    "%(asctime)s %(levelname)s %(name)s " "request_id=%(request_id)s event=%(event)s %(message)s"
)


class RequestIdFilter(logging.Filter):
    """Añade request_id y un nombre de evento por defecto a cada registro."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get() or "-"
        if not hasattr(record, "event"):
            record.event = "app.event"
        return True


@contextmanager
def request_context(request_id: str | None = None) -> Generator[str, None, None]:
    """Asocia un identificador de correlación con todas las operaciones hijas."""

    token = request_id_var.set(request_id or uuid4().hex)
    try:
        yield request_id_var.get() or ""
    finally:
        request_id_var.reset(token)


@contextmanager
def ensure_request_context(request_id: str | None = None) -> Generator[str, None, None]:
    """Reutiliza el contexto activo o crea uno nuevo si no hay ninguno.

    Evita que la GUI muestre un ``request_id`` distinto del ``X-Request-ID``
    que realmente se envió al gateway: el hilo de trabajo abre el contexto y
    el cliente HTTP debe reutilizarlo, no reemplazarlo.
    """

    existing = request_id_var.get()
    if existing:
        yield existing
        return
    with request_context(request_id) as identifier:
        yield identifier


def current_request_id() -> str:
    return request_id_var.get() or "-"


def configure_logging(settings: Settings) -> logging.Logger:
    """Configura stdout (obligatorio) y, si se pide por entorno, un archivo."""

    root = logging.getLogger()
    root.handlers.clear()

    formatter = logging.Formatter(_LOG_FORMAT)
    request_filter = RequestIdFilter()

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(request_filter)
    root.addHandler(stream_handler)

    if settings.log_file is not None:
        path = Path(settings.log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(request_filter)
        root.addHandler(file_handler)

    root.setLevel(settings.log_level)
    return root
