"""Versión de la aplicación (inyectada por el pipeline de release)."""

from __future__ import annotations

import os
from importlib.metadata import PackageNotFoundError, version as distribution_version

_FALLBACK = "0.1.0"


def get_version() -> str:
    """Devuelve PDF_EXTRACT_VERSION, la versión instalada o un fallback.

    Factor V: la versión se fija en el momento del *release*, no en el código.
    """

    from_environment = os.getenv("PDF_EXTRACT_VERSION")
    if from_environment and from_environment.strip():
        return from_environment.strip()
    try:
        return distribution_version("pdf-extractext-gui")
    except PackageNotFoundError:
        return _FALLBACK
