"""Cliente HTTP hardening para el poly-repo de PDF Extractext.

Todas las llamadas pasan por el entrypoint de Traefik salvo configuración
explícita. Incluye:

* autenticación y TLS configurables por entorno;
* ``X-Request-ID`` correlacionable con los logs del backend;
* reintentos con backoff, jitter y ``Retry-After``;
* límites de tamaño en respuestas y en el PDF local;
* cierre de la SAGA por checksum (el upload no devuelve ``document_id``);
* sin redirects automáticos y sin variables de entorno de proxy por defecto.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import requests

from app_logging import current_request_id, ensure_request_context
from app_settings import Settings

logger = logging.getLogger(__name__)

RETRYABLE_STATUSES = frozenset({409, 429, 502, 503, 504})
NON_RETRYABLE_STATUSES = frozenset({400, 401, 403, 404, 413})
PDF_MAGIC = b"%PDF-"


class ApiError(Exception):
    """Error presentable al usuario, con metadatos para resiliencia."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
        retryable: bool = False,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.retryable = retryable
        self.request_id = request_id or current_request_id()


class SagaError(ApiError):
    """La SAGA terminó en error o no convergió dentro del timeout."""


class SagaRetryableError(SagaError):
    """La SAGA fue compensada; volver a subir los mismos bytes la reactiva."""


def _parse_retry_after(value: str | None, default: float) -> float:
    if not value:
        return default
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
            return max(0.0, parsed.timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return default


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ApiClient:
    def __init__(self, settings: Settings, session: requests.Session | None = None) -> None:
        self.settings = settings
        self.session = session or requests.Session()
        self.session.trust_env = settings.trust_env
        self.session.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": f"pdf-extractext-gui/{settings.version}",
                **settings.auth_headers,
            }
        )

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> ApiClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ HTTP

    def _log_path(self, url: str) -> str:
        parsed = urlparse(url)
        return parsed.path or "/"

    def _read_body(self, response: requests.Response) -> dict[str, Any]:
        """Lee un JSON con límite de tamaño; nunca confía en el servidor."""

        limit = self.settings.max_response_bytes
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(chunk_size=16 * 1024):
            total += len(chunk)
            if total > limit:
                raise ApiError(
                    f"El servidor devolvió una respuesta demasiado grande "
                    f"(máximo {limit // 1024 // 1024} MB).",
                    status_code=response.status_code,
                )
            chunks.append(chunk)
        try:
            payload = json.loads(b"".join(chunks).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError("El servidor devolvió una respuesta inválida.") from exc
        if not isinstance(payload, dict):
            raise ApiError("El servidor devolvió una respuesta inválida.")
        return payload

    def _detail(self, response: requests.Response) -> str:
        try:
            payload = self._read_body(response)
        except ApiError:
            return ""
        detail = payload.get("detail", "")
        if not isinstance(detail, str):
            detail = str(detail)
        return detail[:500]

    def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        request_id = current_request_id()
        headers = {"X-Request-ID": request_id, **kwargs.pop("headers", {})}
        started = time.monotonic()

        try:
            response = self.session.request(
                method,
                url,
                headers=headers,
                timeout=(self.settings.connect_timeout, self.settings.read_timeout),
                verify=self.settings.verify,
                allow_redirects=self.settings.allow_redirects,
                stream=True,
                **kwargs,
            )
        except requests.Timeout as exc:
            logger.warning("http.timeout method=%s path=%s", method, self._log_path(url))
            raise ApiError(
                "El servidor tardó demasiado en responder. Intentá nuevamente.",
                retryable=True,
                request_id=request_id,
            ) from exc
        except requests.ConnectionError as exc:
            logger.warning("http.connection_error method=%s path=%s", method, self._log_path(url))
            raise ApiError(
                "No se pudo conectar al servidor. Revisá la URL del gateway en Configuración.",
                retryable=True,
                request_id=request_id,
            ) from exc
        except requests.RequestException as exc:
            logger.warning("http.request_error method=%s error=%s", method, type(exc).__name__)
            raise ApiError(
                f"Error de comunicación: {type(exc).__name__}",
                retryable=True,
                request_id=request_id,
            ) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        logger.info(
            "http.response status=%s duration_ms=%d",
            response.status_code,
            duration_ms,
            extra={"event": "http.response"},
        )

        try:
            if 300 <= response.status_code < 400:
                raise ApiError(
                    "El gateway redirigió la solicitud. Revisá el routing de Traefik "
                    "o habilitá PDF_EXTRACT_ALLOW_REDIRECTS explícitamente.",
                    status_code=response.status_code,
                    request_id=request_id,
                )

            if not response.ok:
                detail = self._detail(response)
                retry_after = _parse_retry_after(
                    response.headers.get("Retry-After"),
                    self.settings.retry_base_delay,
                )
                messages = {
                    400: "La solicitud no es válida.",
                    401: "No se pudo autenticar contra el gateway.",
                    403: "Acceso denegado por el gateway.",
                    404: "El recurso solicitado no existe.",
                    413: "El archivo supera el tamaño permitido.",
                    429: "Demasiadas solicitudes. Intentá nuevamente en unos segundos.",
                    502: "El servicio de extracción no está disponible.",
                    503: "El servicio está saturado. Intentá nuevamente en unos segundos.",
                    504: "La extracción tardó demasiado.",
                }
                raise ApiError(
                    detail or messages.get(response.status_code, f"Error {response.status_code}."),
                    status_code=response.status_code,
                    retry_after=retry_after,
                    retryable=response.status_code in RETRYABLE_STATUSES,
                    request_id=request_id,
                )

            return self._read_body(response)
        finally:
            response.close()

    def _call(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        """Ejecuta una petición con reintentos acotados y backoff con jitter."""

        last_error: ApiError | None = None
        for attempt in range(1, self.settings.retry_attempts + 1):
            try:
                return self._request(method, url, **kwargs)
            except ApiError as exc:
                last_error = exc
                retriable = exc.retryable or (
                    exc.status_code is not None and exc.status_code in RETRYABLE_STATUSES
                )
                if not retriable or attempt == self.settings.retry_attempts:
                    raise

                delay = (
                    exc.retry_after
                    if exc.retry_after is not None
                    else min(
                        self.settings.retry_max_delay,
                        self.settings.retry_base_delay * (2 ** (attempt - 1)),
                    )
                )
                delay = min(delay, self.settings.retry_max_delay)
                delay += random.uniform(0, self.settings.retry_base_delay / 2)
                logger.warning(
                    "http.retry attempt=%d delay=%.2fs status=%s",
                    attempt,
                    delay,
                    exc.status_code,
                    extra={"event": "http.retry"},
                )
                time.sleep(delay)

        assert last_error is not None
        raise last_error

    # --------------------------------------------------------------- Helpers

    def _validate_pdf(self, path: str | Path) -> Path:
        pdf = Path(path)
        if not pdf.is_file():
            raise ApiError("No se pudo leer el archivo: no existe o no es un archivo.")
        size = pdf.stat().st_size
        if size == 0:
            raise ApiError("El archivo PDF está vacío.")
        if size > self.settings.max_pdf_bytes:
            limit = self.settings.max_pdf_bytes // 1024 // 1024
            raise ApiError(f"El archivo supera el tamaño permitido ({limit} MB).")
        with pdf.open("rb") as stream:
            if stream.read(len(PDF_MAGIC)) != PDF_MAGIC:
                raise ApiError("El archivo seleccionado no es un PDF válido.")
        return pdf

    def _upload(self, pdf: Path, checksum: str) -> dict[str, Any]:
        self._validate_pdf(pdf)
        with pdf.open("rb") as stream:
            files = {"file": (pdf.name, stream, "application/pdf")}
            logger.info(
                "saga.upload checksum=%s",
                checksum,
                extra={"event": "saga.upload"},
            )
            return self._call("POST", f"{self.settings.documents_url}/upload", files=files)

    # -------------------------------------------------------------- Public API

    def extract(self, path: str | Path, output_format: str) -> str:
        if output_format not in ("txt", "md"):
            raise ValueError("Formato de extracción inválido.")
        pdf = self._validate_pdf(path)

        if output_format == "md":
            # Ruta directa al microservicio de extracción: no crea documento ni
            # dispara la SAGA. Queda registrada como decisión explícita.
            with pdf.open("rb") as stream:
                data = self._call(
                    "POST",
                    self.settings.extract_url,
                    files={"file": (pdf.name, stream, "application/pdf")},
                )
            content = data.get("content")
            if not isinstance(content, str):
                raise ApiError("El servidor no devolvió texto en el formato esperado.")
            logger.info("extract.direct format=md", extra={"event": "extract.direct"})
            return content

        checksum = _sha256(pdf)
        with ensure_request_context():
            for attempt in range(2):
                upload = self._upload(pdf, checksum)
                if not self.settings.saga_enabled:
                    text = upload.get("extracted_text")
                    if not isinstance(text, str):
                        raise ApiError("El servidor no devolvió texto en el formato esperado.")
                    return text
                try:
                    document = self._wait_for_saga(checksum)
                except SagaRetryableError:
                    if attempt == 0:
                        logger.warning(
                            "saga.reupload checksum=%s",
                            checksum,
                            extra={"event": "saga.reupload"},
                        )
                        continue
                    raise SagaError(
                        "La extracción falló dos veces. Revisá el PDF o intentá con otro archivo."
                    ) from None
                text = document.get("txt_contenido")
                if not isinstance(text, str):
                    raise SagaError("El documento finalizado no contiene texto.")
                return text
        raise AssertionError("unreachable")

    def _wait_for_saga(self, checksum: str) -> dict[str, Any]:
        """Consulta el estado de la SAGA hasta que converja o expire."""

        url = f"{self.settings.documents_url}/by-checksum/{quote(checksum, safe='')}"
        deadline = time.monotonic() + self.settings.saga_timeout

        while time.monotonic() < deadline:
            try:
                payload = self._call("GET", url)
            except ApiError as exc:
                if exc.status_code == 404:
                    logger.debug("saga.not_visible_yet", extra={"event": "saga.poll"})
                    time.sleep(self.settings.saga_poll_interval)
                    continue
                raise

            document = payload.get("document")
            if not isinstance(document, dict):
                raise SagaError("El servidor devolvió un estado de SAGA inválido.")

            state = document.get("estado")
            if state == "ok":
                logger.info("saga.completed", extra={"event": "saga.completed"})
                return document

            if state == "error":
                detail = str(document.get("last_error") or "")[:200]
                logger.warning(
                    "saga.compensated detail=%s",
                    detail,
                    extra={"event": "saga.compensated"},
                )
                # El backend ya compensó; el re-POST con los mismos bytes
                # reactiva la SAGA sin duplicar el documento (SHA-256).
                raise SagaRetryableError(
                    "La extracción falló y fue compensada. Se reintentará automáticamente."
                )

            time.sleep(self.settings.saga_poll_interval)

        raise SagaError("La extracción no terminó a tiempo. Consultá el Historial en unos minutos.")

    def history(self, skip: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        if not 0 <= skip <= 1_000_000 or not 1 <= limit <= 100:
            raise ValueError("Parámetros de paginación inválidos.")
        data = self._call(
            "GET",
            f"{self.settings.documents_url}",
            params={"skip": skip, "limit": limit},
        )
        items = data.get("items")
        if not isinstance(items, list):
            raise ApiError("El servidor devolvió un historial inválido.")
        return items

    def document(self, doc_id: str) -> dict[str, Any]:
        data = self._call(
            "GET",
            f"{self.settings.documents_url}/{quote(str(doc_id), safe='')}",
            params={"include_text": "true"},
        )
        document = data.get("document")
        if not isinstance(document, dict) or not isinstance(document.get("txt_contenido"), str):
            raise ApiError("El servidor devolvió un documento inválido.")
        return document

    def rename(self, doc_id: str, name: str) -> dict[str, Any]:
        clean = str(name).strip()
        if not 1 <= len(clean) <= 200:
            raise ValueError("El nombre debe tener entre 1 y 200 caracteres.")
        return self._call(
            "PATCH",
            f"{self.settings.documents_url}/{quote(str(doc_id), safe='')}",
            json={"pdf_nombre": clean},
        )

    def delete(self, doc_id: str) -> dict[str, Any]:
        return self._call(
            "DELETE",
            f"{self.settings.documents_url}/{quote(str(doc_id), safe='')}",
        )
