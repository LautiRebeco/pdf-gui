"""Paso de BUILD separado de RELEASE y RUN (factor V).

Genera un artefacto inmutable en ``dist/`` con la versión inyectada por el
pipeline. No instala nada ni modifica el entorno de ejecución.
"""

from __future__ import annotations

import json
import os
import platform
import sys
import zipfile
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from version import get_version

SOURCE_FILES = (
    "api_client.py",
    "app_logging.py",
    "app_settings.py",
    "health.py",
    "interface.py",
    "version.py",
    "pyproject.toml",
    "uv.lock",
    "README.md",
    ".env.example",
    "scripts/__init__.py",
    "scripts/admin.py",
    "scripts/build.py",
    "scripts/release.py",
)


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    version = get_version()
    root = Path(__file__).resolve().parent.parent
    dist = root / "dist"
    dist.mkdir(exist_ok=True)
    archive = dist / f"pdf-extractext-gui-{version}.zip"

    missing = [name for name in SOURCE_FILES if not (root / name).is_file()]
    if missing:
        print(f"Faltan archivos para el build: {missing}", file=sys.stderr)
        return 1

    raw_epoch = os.getenv("SOURCE_DATE_EPOCH", "").strip()
    epoch = int(raw_epoch) if raw_epoch else 0
    if epoch:
        fixed_date = datetime.fromtimestamp(epoch, tz=timezone.utc).timetuple()[:6]
    else:
        fixed_date = (1980, 1, 1, 0, 0, 0)

    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name in SOURCE_FILES:
            info = zipfile.ZipInfo(name, date_time=fixed_date)
            info.external_attr = 0o644 << 16
            bundle.writestr(info, (root / name).read_bytes())

        bundle.writestr(
            "build-info.json",
            json.dumps(
                {
                    "version": version,
                    "python": platform.python_version(),
                    "built_at": datetime.now(timezone.utc).isoformat(),
                    "artifact": archive.name,
                },
                indent=2,
            ),
        )

    print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
