"""La configuración solo puede venir del entorno y debe ser segura."""

from __future__ import annotations

import pytest

from app_settings import ConfigError, load_settings, valid_url

BASE_ENV = {"PDF_EXTRACT_GATEWAY_URL": "https://gateway.local"}


def test_traefik_por_defecto_deriva_las_dos_urls():
    settings = load_settings(BASE_ENV)
    assert settings.documents_url == "https://gateway.local/documents"
    assert settings.extract_url == "https://gateway.local/extract"
    assert settings.gateway_url == "https://gateway.local"


def test_alias_legados_siguen_funcionando():
    settings = load_settings(
        {
            "API_URL": "https://gateway.local",
            "DOCUMENTS_API_URL": "https://documentos.local",
            "EXTRACT_API_URL": "https://extract.local",
        }
    )
    assert settings.documents_url == "https://documentos.local"
    assert settings.extract_url == "https://extract.local"


def test_urls_canonicas_ganan_a_los_alias():
    settings = load_settings(
        {
            "PDF_EXTRACT_GATEWAY_URL": "https://gateway.local",
            "DOCUMENTS_API_URL": "http://viejo.local",
            "PDF_EXTRACT_DOCUMENTS_URL": "https://nuevo.local/documents",
        }
    )
    assert settings.documents_url == "https://nuevo.local/documents"


def test_http_remoto_se_rechaza_sin_tls():
    with pytest.raises(ConfigError, match="válida y segura"):
        load_settings({"PDF_EXTRACT_GATEWAY_URL": "http://gateway.local"})


def test_http_loopback_se_permite_para_desarrollo():
    settings = load_settings({"API_URL": "http://127.0.0.1:8080"})
    assert settings.gateway_url == "http://127.0.0.1:8080"


def test_auth_bearer_no_aparece_en_la_ui():
    settings = load_settings({**BASE_ENV, "PDF_EXTRACT_AUTH_TOKEN": "super-secreto"})
    assert settings.auth_headers["Authorization"] == "Bearer super-secreto"
    assert "super-secreto" not in str(settings.public_dict())


def test_auth_basic():
    settings = load_settings(
        {
            **BASE_ENV,
            "PDF_EXTRACT_BASIC_USER": "gui",
            "PDF_EXTRACT_BASIC_PASSWORD": "clave",
        }
    )
    assert settings.auth_headers["Authorization"].startswith("Basic ")


def test_basic_requiere_usuario_y_password():
    with pytest.raises(ConfigError, match="deben ir juntos"):
        load_settings({**BASE_ENV, "PDF_EXTRACT_BASIC_USER": "gui"})


def test_ca_bundle_debe_existir(tmp_path):
    with pytest.raises(ConfigError, match="CA_BUNDLE"):
        load_settings({**BASE_ENV, "PDF_EXTRACT_CA_BUNDLE": str(tmp_path / "no-existe.pem")})
    ca = tmp_path / "ca.pem"
    ca.write_text("cert", encoding="utf-8")
    settings = load_settings({**BASE_ENV, "PDF_EXTRACT_CA_BUNDLE": str(ca)})
    assert settings.verify == str(ca)


@pytest.mark.parametrize(
    "url",
    [
        "incorrecto",
        "ftp://servidor",
        "http://",
        "http://servidor:abc",
        "http://usuario:clave@servidor",
        "http://servidor?consulta=1",
        "https://servidor#fragmento",
        "https://servidor/../admin",
        "https://servidor/\n",
    ],
)
def test_url_invalida(url):
    assert not valid_url(url)


@pytest.mark.parametrize(
    "variable,valor",
    [
        ("PDF_EXTRACT_READ_TIMEOUT", "0"),
        ("PDF_EXTRACT_MAX_RESPONSE_BYTES", "abc"),
        ("PDF_EXTRACT_HEALTH_PORT", "70000"),
        ("PDF_EXTRACT_THEME", "rosa"),
        ("PDF_EXTRACT_ALLOW_REDIRECTS", "quizas"),
    ],
)
def test_valores_numericos_y_enums_invalidos(variable, valor):
    with pytest.raises(ConfigError):
        load_settings({**BASE_ENV, variable: valor})


def test_no_hay_archivo_de_configuracion():
    assert not hasattr(load_settings(BASE_ENV), "config_path")
