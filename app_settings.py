"""Configuración de la interfaz.

Factor III de 12-factor: *toda* la configuración proviene del entorno. No se
lee ni se escribe ningún archivo de preferencias: el directorio de
configuración del usuario ya no existe. Las URL apuntan, por defecto, al
entrypoint de Traefik del poly-repo (``/documents`` y ``/extract``).
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from version import get_version

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
VALID_THEMES = frozenset({"dark", "light", "system"})
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


class ConfigError(ValueError):
    """La configuración de entorno es inválida o insegura."""


def _read(env: dict[str, str], *names: str, default: str = "") -> str:
    for name in names:
        value = env.get(name)
        if value is not None and value.strip():
            return value.strip()
    return default


def _number(
    env: dict[str, str],
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} debe ser un número, se recibió {raw!r}.") from exc
    if not minimum <= value <= maximum:
        raise ConfigError(f"{name} debe estar entre {minimum} y {maximum} (se recibió {value}).")
    return value


def _integer(
    env: dict[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    return int(
        _number(
            env,
            name,
            default,
            minimum=minimum,
            maximum=maximum,
        )
    )


def _boolean(env: dict[str, str], name: str, default: bool = False) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} debe ser true/false, se recibió {raw!r}.")


def valid_url(value: str, *, allow_http_loopback: bool = True) -> bool:
    """Valida una URL base de servicio.

    Se rechazan credenciales embebidas, query/fragmento, caracteres de control
    y (salvo loopback explícito) HTTP en claro: con Traefik el TLS se termina
    en el gateway y el cliente debe hablar HTTPS.
    """

    if not isinstance(value, str):
        return False
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        return False
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"}:
        return False
    if not parsed.hostname or parsed.username or parsed.password:
        return False
    if parsed.query or parsed.fragment:
        return False
    if port is not None and not 1 <= port <= 65535:
        return False
    if ".." in parsed.path.split("/"):
        return False
    if parsed.scheme == "http" and not (
        allow_http_loopback and parsed.hostname.lower() in LOOPBACK_HOSTS
    ):
        return False
    return True


def _base_url(env: dict[str, str], canonical: str, alias: str, default: str) -> str:
    value = _read(env, canonical, alias, default=default).rstrip("/")
    if not valid_url(value):
        raise ConfigError(
            f"{canonical} no es una URL válida y segura: {value!r}. "
            "Usá https:// para hosts remotos (Traefik termina el TLS)."
        )
    return value


def _auth_headers(env: dict[str, str]) -> dict[str, str]:
    """Construye headers de autenticación para Traefik sin exponer secretos."""

    header = _read(env, "PDF_EXTRACT_AUTH_HEADER", default="Authorization")
    if any(ord(char) < 0x20 for char in header) or ":" in header:
        raise ConfigError("PDF_EXTRACT_AUTH_HEADER no es un nombre de header válido.")

    raw_value = env.get("PDF_EXTRACT_AUTH_VALUE")
    basic_user = env.get("PDF_EXTRACT_BASIC_USER")
    basic_password = env.get("PDF_EXTRACT_BASIC_PASSWORD")
    token = env.get("PDF_EXTRACT_AUTH_TOKEN")

    if raw_value and raw_value.strip():
        return {header: raw_value.strip()}

    if basic_user is not None or basic_password is not None:
        if not basic_user or basic_password is None:
            raise ConfigError(
                "PDF_EXTRACT_BASIC_USER y PDF_EXTRACT_BASIC_PASSWORD deben ir juntos."
            )
        credentials = f"{basic_user}:{basic_password}".encode("utf-8")
        encoded = base64.b64encode(credentials).decode("ascii")
        return {header: f"Basic {encoded}"}

    if token and token.strip():
        scheme = _read(env, "PDF_EXTRACT_AUTH_SCHEME", default="Bearer").strip()
        return {header: f"{scheme} {token.strip()}".strip()}

    return {}


@dataclass(frozen=True)
class Settings:
    """Configuración inmutable, validada una única vez al arrancar."""

    gateway_url: str
    documents_url: str
    extract_url: str
    theme: str
    connect_timeout: float
    read_timeout: float
    max_response_bytes: int
    max_pdf_bytes: int
    verify: bool | str
    auth_headers: dict[str, str] = field(default_factory=dict, repr=False)
    allow_redirects: bool = False
    trust_env: bool = False
    retry_attempts: int = 3
    retry_base_delay: float = 0.5
    retry_max_delay: float = 5.0
    saga_enabled: bool = True
    saga_poll_interval: float = 1.0
    saga_timeout: float = 180.0
    log_level: str = "INFO"
    log_file: Path | None = None
    health_host: str | None = None
    health_port: int = 0
    max_workers: int = 4
    version: str = "0.1.0"

    def public_dict(self) -> dict[str, object]:
        """Descripción segura para la UI y los logs (sin secretos)."""

        return {
            "PDF_EXTRACT_GATEWAY_URL": self.gateway_url,
            "PDF_EXTRACT_DOCUMENTS_URL": self.documents_url,
            "PDF_EXTRACT_EXTRACT_URL": self.extract_url,
            "PDF_EXTRACT_THEME": self.theme,
            "PDF_EXTRACT_CONNECT_TIMEOUT": self.connect_timeout,
            "PDF_EXTRACT_READ_TIMEOUT": self.read_timeout,
            "PDF_EXTRACT_MAX_RESPONSE_BYTES": self.max_response_bytes,
            "PDF_EXTRACT_MAX_PDF_BYTES": self.max_pdf_bytes,
            "PDF_EXTRACT_CA_BUNDLE": (
                self.verify if isinstance(self.verify, str) else "(certificados del sistema)"
            ),
            "PDF_EXTRACT_AUTH": "configurado" if self.auth_headers else "no configurado",
            "PDF_EXTRACT_ALLOW_REDIRECTS": self.allow_redirects,
            "PDF_EXTRACT_TRUST_ENV": self.trust_env,
            "PDF_EXTRACT_RETRY_ATTEMPTS": self.retry_attempts,
            "PDF_EXTRACT_SAGA_ENABLED": self.saga_enabled,
            "PDF_EXTRACT_LOG_LEVEL": self.log_level,
            "PDF_EXTRACT_HEALTH_PORT": self.health_port,
            "PDF_EXTRACT_MAX_WORKERS": self.max_workers,
        }


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Lee y valida toda la configuración desde el entorno."""

    environment = dict(os.environ if env is None else env)

    gateway = _base_url(
        environment,
        "PDF_EXTRACT_GATEWAY_URL",
        "API_URL",
        default="http://127.0.0.1:8080",
    )
    documents = _base_url(
        environment,
        "PDF_EXTRACT_DOCUMENTS_URL",
        "DOCUMENTS_API_URL",
        default=f"{gateway}/documents",
    )
    extract = _base_url(
        environment,
        "PDF_EXTRACT_EXTRACT_URL",
        "EXTRACT_API_URL",
        default=f"{gateway}/extract",
    )

    theme = _read(environment, "PDF_EXTRACT_THEME", default="dark").lower()
    if theme not in VALID_THEMES:
        raise ConfigError(f"PDF_EXTRACT_THEME debe ser uno de {sorted(VALID_THEMES)}.")

    log_level = _read(environment, "PDF_EXTRACT_LOG_LEVEL", default="INFO").upper()
    if log_level not in VALID_LOG_LEVELS:
        raise ConfigError(f"PDF_EXTRACT_LOG_LEVEL debe ser uno de {sorted(VALID_LOG_LEVELS)}.")

    ca_bundle = _read(environment, "PDF_EXTRACT_CA_BUNDLE", default="")
    if ca_bundle:
        ca_path = Path(ca_bundle).expanduser()
        if not ca_path.is_file():
            raise ConfigError(f"PDF_EXTRACT_CA_BUNDLE no existe: {ca_path}.")
        verify: bool | str = str(ca_path)
    else:
        verify = True

    log_file_raw = _read(environment, "PDF_EXTRACT_LOG_FILE", default="")
    log_file = Path(log_file_raw).expanduser() if log_file_raw else None

    health_host_raw = _read(environment, "PDF_EXTRACT_HEALTH_HOST", default="")
    health_host = health_host_raw or None
    health_port = _integer(
        environment,
        "PDF_EXTRACT_HEALTH_PORT",
        0,
        minimum=0,
        maximum=65535,
    )

    return Settings(
        gateway_url=gateway,
        documents_url=documents,
        extract_url=extract,
        theme=theme,
        connect_timeout=_number(
            environment,
            "PDF_EXTRACT_CONNECT_TIMEOUT",
            5.0,
            minimum=0.1,
            maximum=60.0,
        ),
        read_timeout=_number(
            environment,
            "PDF_EXTRACT_READ_TIMEOUT",
            120.0,
            minimum=1.0,
            maximum=600.0,
        ),
        max_response_bytes=_integer(
            environment,
            "PDF_EXTRACT_MAX_RESPONSE_BYTES",
            20 * 1024 * 1024,
            minimum=1024,
            maximum=200 * 1024 * 1024,
        ),
        max_pdf_bytes=_integer(
            environment,
            "PDF_EXTRACT_MAX_PDF_BYTES",
            10 * 1024 * 1024,
            minimum=1024,
            maximum=100 * 1024 * 1024,
        ),
        verify=verify,
        auth_headers=_auth_headers(environment),
        allow_redirects=_boolean(environment, "PDF_EXTRACT_ALLOW_REDIRECTS", False),
        trust_env=_boolean(environment, "PDF_EXTRACT_TRUST_ENV", False),
        retry_attempts=_integer(
            environment,
            "PDF_EXTRACT_RETRY_ATTEMPTS",
            3,
            minimum=1,
            maximum=10,
        ),
        retry_base_delay=_number(
            environment,
            "PDF_EXTRACT_RETRY_BASE_DELAY",
            0.5,
            minimum=0.0,
            maximum=10.0,
        ),
        retry_max_delay=_number(
            environment,
            "PDF_EXTRACT_RETRY_MAX_DELAY",
            5.0,
            minimum=0.1,
            maximum=60.0,
        ),
        saga_enabled=_boolean(environment, "PDF_EXTRACT_SAGA_ENABLED", True),
        saga_poll_interval=_number(
            environment,
            "PDF_EXTRACT_SAGA_POLL_INTERVAL",
            1.0,
            minimum=0.1,
            maximum=30.0,
        ),
        saga_timeout=_number(
            environment,
            "PDF_EXTRACT_SAGA_TIMEOUT",
            180.0,
            minimum=5.0,
            maximum=3600.0,
        ),
        log_level=log_level,
        log_file=log_file,
        health_host=health_host,
        health_port=health_port,
        max_workers=_integer(
            environment,
            "PDF_EXTRACT_MAX_WORKERS",
            4,
            minimum=1,
            maximum=32,
        ),
        version=_read(environment, "PDF_EXTRACT_VERSION", default=get_version()),
    )
