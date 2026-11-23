# pdf-extractext-gui

Cliente de escritorio (Tkinter/CustomTkinter) del poly-repo **PDF Extractext**.
Se conecta al **gateway Traefik** del orquestador, que enruta:

* `/documents*` → servicio de documentos (el que persiste en MongoDB).
* `/extract*` → servicio de extracción de texto de PDF.

No hay configuración local ni archivo de preferencias: **todo se lee del entorno**
al arrancar. La pestaña *Configuración* muestra los valores efectivos (sin
secretos) y permite copiar una plantilla.

## Ejecutar

```bash
cp .env.example .env
set -a && . ./.env && set +a
uv sync --frozen --all-groups
uv run python interface.py
```

El `.env` **no** es leído por la aplicación: solo por tu shell o por el
orquestador. La app solo mira `os.environ`.

### Modo desarrollo sin Traefik

```bash
export PDF_EXTRACT_GATEWAY_URL=http://127.0.0.1:8000
# o apuntar directo a cada servicio:
export PDF_EXTRACT_DOCUMENTS_URL=http://127.0.0.1:8002/documents
export PDF_EXTRACT_EXTRACT_URL=http://127.0.0.1:8001/extract
```

HTTP solo se permite en `localhost`/`127.0.0.1`/`::1`; para cualquier otro host
la app exige HTTPS (Traefik termina TLS).

## SAGA y resiliencia

`POST /documents/upload` inicia la SAGA en el servicio de documentos, pero su
respuesta **no incluye** `document_id`. Por eso el cliente:

1. calcula SHA-256 local del PDF,
2. reintenta el upload ante `409/502/503/504` respetando `Retry-After`,
3. hace polling de `GET /documents/by-checksum/{sha256}` hasta `estado=ok|error`,
4. si la SAGA fue compensada (`error`), vuelve a subir los mismos bytes — el
   backend los deduplica por checksum — una única vez,
5. propaga `X-Request-ID` en cada petición para correlacionar logs.

El formato *Markdown* llama directamente a `/extract` (no crea documento ni
SAGA); es una decisión explícita y queda registrada en los logs.

## Seguridad

* TLS verificado siempre; CA interna vía `PDF_EXTRACT_CA_BUNDLE`.
* Autenticación Bearer/Basic/custom configurable por entorno; el valor nunca se
  loguea ni se persiste.
* Redirects desactivados por defecto; `trust_env=False` (ni proxy ni `.netrc`
  del entorno) salvo opt-in explícito.
* Límite de tamaño para respuestas HTTP y para el PDF local; validación de
  magic bytes `%PDF-`.
* `doc_id` y checksum se codifican con `quote()` antes de ir al path.

## 12-factor

| Factor | Cómo se cumple |
|---|---|
| I Codebase | Un repo por servicio en el poly-repo; dependencias nunca vendidas. |
| II Dependencies | `pyproject.toml` + `uv.lock` con hashes; grupos `dev` separados. |
| III Config | Solo entorno; ver `.env.example`. Sin archivos de preferencias. |
| IV Backing services | URL de cada servicio derivadas del gateway y sobreescribibles por entorno. |
| V Build/release/run | `scripts/build.py`, `scripts/release.py` y CI separan artefacto, publicación y ejecución. |
| VI Processes | Proceso sin estado propio: la SAGA y el historial viven en el backend. |
| VII Port binding | `PDF_EXTRACT_HEALTH_PORT` expone `/health` y `/ready` para el supervisor. |
| VIII Concurrency | `ThreadPoolExecutor` acotado por `PDF_EXTRACT_MAX_WORKERS`. |
| IX Disposability | `WM_DELETE_WINDOW` y SIGTERM cancelan trabajos, cierran el pool y el health server. |
| X Dev/prod parity | Mismo Python, mismo lock y mismos tests en local, Docker y CI. |
| XI Logs | JSON-like a stdout con `request_id`/`event`; archivo opcional por entorno. |
| XII Admin processes | `python -m scripts.admin ...` usa exactamente el mismo código y entorno. |

## Verificación

```bash
uv run pytest -q
uv run black --check .
uv run flake8 .
uv run pip-audit
```
