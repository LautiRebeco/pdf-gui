"""Paso de RELEASE (factor V): publica el artefacto ya construido.

No recompila nada: solo valida el ZIP, calcula su SHA-256 y escribe un
manifiesto que el orquestador puede consumir para fijar la versión.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from version import get_version


def main(argv: Sequence[str] | None = None) -> int:
    version = get_version()
    root = Path(__file__).resolve().parent.parent
    artifact = root / "dist" / f"pdf-extractext-gui-{version}.zip"

    if not artifact.is_file():
        print(f"No existe el artefacto {artifact}. Ejecutá primero el build.", file=sys.stderr)
        return 1

    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    manifest = {
        "artifact": artifact.name,
        "version": version,
        "sha256": digest,
        "size_bytes": artifact.stat().st_size,
    }
    target = root / "dist" / "release-manifest.json"
    target.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
