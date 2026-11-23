"""Procesos administrativos de una sola ejecución (factor XII).

Se ejecutan con el MISMO entorno y el MISMO código que la GUI:

    python -m scripts.admin check-config
    python -m scripts.admin history --limit 5
    python -m scripts.admin delete --id <id> --yes
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from api_client import ApiClient, ApiError
from app_logging import configure_logging
from app_settings import ConfigError, load_settings


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pdf-extractext-admin")
    parser.add_argument("command", choices=["check-config", "history", "document", "delete"])
    parser.add_argument("--id", help="document_id")
    parser.add_argument("--skip", type=int, default=0)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--yes", action="store_true", help="confirmar borrado")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings)

    if args.command == "check-config":
        print(json.dumps(settings.public_dict(), indent=2, ensure_ascii=False))
        return 0

    with ApiClient(settings) as client:
        try:
            if args.command == "history":
                result = client.history(args.skip, args.limit)
            elif args.command == "document":
                if not args.id:
                    print("--id es obligatorio", file=sys.stderr)
                    return 2
                result = client.document(args.id)
            elif args.command == "delete":
                if not args.id or not args.yes:
                    print("delete requiere --id y --yes", file=sys.stderr)
                    return 2
                result = client.delete(args.id)
            else:
                return 2
        except ApiError as exc:
            print(f"Error {exc.status_code}: {exc}", file=sys.stderr)
            return 1
        except ValueError as exc:
            print(f"Argumento inválido: {exc}", file=sys.stderr)
            return 2

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
