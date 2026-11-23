"""Cliente HTTP: contrato Traefik, SAGA y controles de seguridad."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import Mock

import pytest
import requests

from api_client import ApiClient, ApiError
from app_logging import request_context
from app_settings import load_settings

GATEWAY = {"PDF_EXTRACT_GATEWAY_URL": "https://gateway.local"}
PDF_BYTES = b"%PDF-1.4\ncontenido"


def settings(**overrides):
    return replace(load_settings(GATEWAY), **overrides)


def response(data, status=200, headers=None):
    result = Mock()
    result.status_code = status
    result.ok = status < 400
    result.headers = headers or {}
    result.iter_content.return_value = [json.dumps(data).encode("utf-8")]
    return result


def session(*responses):
    mock = Mock(spec=requests.Session)
    mock.headers = {}
    mock.request.side_effect = list(responses)
    return mock


def pdf(tmp_path, name="informe.pdf"):
    path = tmp_path / name
    path.write_bytes(PDF_BYTES)
    return path


def call(mock, index):
    return mock.request.call_args_list[index]


def test_txt_cierra_la_saga_por_checksum(tmp_path):
    sesion = session(
        response({"status": "uploaded"}),
        response({"document": {"estado": "pendiente"}}),
        response({"document": {"estado": "ok", "txt_contenido": "hola"}}),
    )
    cliente = ApiClient(settings(saga_poll_interval=0.01), session=sesion)

    assert cliente.extract(pdf(tmp_path), "txt") == "hola"

    assert call(sesion, 0).args == ("POST", "https://gateway.local/documents/upload")
    assert call(sesion, 1).args[0] == "GET"
    assert call(sesion, 1).args[1].startswith("https://gateway.local/documents/by-checksum/")
    assert call(sesion, 2).args[1] == call(sesion, 1).args[1]
    for index in range(3):
        options = call(sesion, index).kwargs
        assert options["headers"]["X-Request-ID"]
        assert options["timeout"] == (5.0, 120.0)
        assert options["verify"] is True
        assert options["allow_redirects"] is False
        assert options["stream"] is True


def test_markdown_va_directo_al_servicio_de_extraccion(tmp_path):
    sesion = session(response({"content": "# Titulo"}))
    cliente = ApiClient(settings(), session=sesion)

    assert cliente.extract(pdf(tmp_path), "md") == "# Titulo"
    assert call(sesion, 0).args == ("POST", "https://gateway.local/extract")


def test_reintento_respeta_retry_after(mocker):
    dormir = mocker.patch("api_client.time.sleep")
    sesion = session(
        response({"detail": "saturado"}, 503, {"Retry-After": "0.2"}),
        response({"items": []}),
    )
    cliente = ApiClient(settings(retry_attempts=2, retry_max_delay=1.0), session=sesion)

    assert cliente.history() == []
    assert sesion.request.call_count == 2
    dormir.assert_called_once()
    assert dormir.call_args.args[0] >= 0.2


def test_error_413_no_se_reintenta():
    sesion = session(response({"detail": "grande"}, 413))
    cliente = ApiClient(settings(), session=sesion)
    with pytest.raises(ApiError, match="grande"):
        cliente.history()
    assert sesion.request.call_count == 1


def test_redirect_bloqueado():
    sesion = session(response({}, 302, {"Location": "https://otro.local"}))
    cliente = ApiClient(settings(), session=sesion)
    with pytest.raises(ApiError, match="redirigió"):
        cliente.history()
    assert call(sesion, 0).kwargs["allow_redirects"] is False


def test_respuesta_excede_el_limite():
    sesion = session(response({"items": ["x" * 100]}))
    cliente = ApiClient(settings(max_response_bytes=32), session=sesion)
    with pytest.raises(ApiError, match="demasiado grande"):
        cliente.history()


def test_pdf_sin_magic_bytes_se_rechaza(tmp_path):
    archivo = tmp_path / "malo.pdf"
    archivo.write_bytes("no es pdf".encode())
    cliente = ApiClient(settings(), session=session())
    with pytest.raises(ApiError, match="no es un PDF"):
        cliente.extract(archivo, "txt")


def test_saga_compensada_hace_reupload(tmp_path, mocker):
    mocker.patch("api_client.time.sleep")
    sesion = session(
        response({"status": "uploaded"}),
        response({"document": {"estado": "error", "last_error": "boom"}}),
        response({"status": "uploaded"}),
        response({"document": {"estado": "ok", "txt_contenido": "recuperado"}}),
    )
    cliente = ApiClient(settings(), session=sesion)

    assert cliente.extract(pdf(tmp_path), "txt") == "recuperado"
    uploads = [
        call for call in sesion.request.call_args_list if call.args[1].endswith("/documents/upload")
    ]
    assert len(uploads) == 2


def test_doc_id_se_codifica_en_el_path():
    sesion = session(response({"document": {"txt_contenido": "hola"}}))
    cliente = ApiClient(settings(), session=sesion)
    cliente.document("../admin?x=1")
    url = call(sesion, 0).args[1]
    assert "%2F" in url
    assert "?x=1" not in url


def test_paginacion_y_nombre_validados():
    cliente = ApiClient(settings(), session=session())
    with pytest.raises(ValueError):
        cliente.history(limit=500)
    with pytest.raises(ValueError):
        cliente.rename("abc", "")


def test_timeout_y_connection_error_son_retryables():
    sesion = Mock(spec=requests.Session)
    sesion.headers = {}
    sesion.request.side_effect = requests.Timeout()
    cliente = ApiClient(settings(), session=sesion)
    with pytest.raises(ApiError) as timeout:
        cliente.history()
    assert timeout.value.retryable is True

    sesion.request.side_effect = requests.ConnectionError()
    with pytest.raises(ApiError) as connection:
        cliente.history()
    assert connection.value.retryable is True


def test_reutiliza_el_request_id_del_contexto_activo(tmp_path):
    """El id que ve el usuario debe ser el mismo X-Request-ID enviado."""

    sesion = session(response({"extracted_text": "texto"}))
    cliente = ApiClient(settings(saga_enabled=False), session=sesion)

    with request_context("id-del-hilo-123"):
        cliente.extract(pdf(tmp_path), "txt")

    assert call(sesion, 0).kwargs["headers"]["X-Request-ID"] == "id-del-hilo-123"


def test_sin_saga_devuelve_texto_del_upload(tmp_path):
    sesion = session(response({"extracted_text": "directo"}))
    cliente = ApiClient(settings(saga_enabled=False), session=sesion)
    assert cliente.extract(pdf(tmp_path), "txt") == "directo"
    assert sesion.request.call_count == 1
