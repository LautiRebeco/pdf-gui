# pdf-gui

Cliente de escritorio de **PDF Extractext**, hecho en Python con CustomTkinter. Podés abrir un PDF, extraer su contenido como texto plano o Markdown y editar el resultado antes de copiarlo o guardarlo. Desde **Historial** podés volver a abrir el texto de un documento guardado, renombrarlo o eliminarlo.

## Estructura

```text
interface.py      Ventana, extracción, historial y configuración
api_client.py     Peticiones a los servicios y seguimiento de la extracción
app_settings.py   Lectura y validación de las variables de entorno
tests/            Pruebas de la interfaz, el cliente HTTP y la configuración
```

## Cómo funciona la extracción

La GUI envía las peticiones a Traefik. El gateway dirige `/documents` al servicio de documentos y `/extract` al extractor.

- **Texto plano.** La GUI sube el PDF a `POST /documents/upload` y consulta el estado del procesamiento por el SHA-256 del archivo. Si el servicio revierte la operación, la GUI vuelve a intentar la carga una vez. El documento procesado queda disponible en el historial.
- **Markdown.** La GUI envía el PDF a `POST /extract` y muestra el contenido que devuelve el extractor. Este flujo no agrega una entrada al historial.

## Puesta en marcha

Necesitás Python 3.10 o posterior y `uv`. Para correr el sistema en local, levantá primero los servicios con Docker Compose desde el [orquestador](https://github.com/LautiRebeco/pdf-extract-orchestrator). Después, desde la carpeta de la GUI, configurá la URL del gateway e iniciá la aplicación.

**PowerShell**

```powershell
$env:PDF_EXTRACT_GATEWAY_URL = "http://127.0.0.1:8000"
uv sync --frozen
uv run python interface.py
```

**Bash**

```bash
export PDF_EXTRACT_GATEWAY_URL=http://127.0.0.1:8000
uv sync --frozen
uv run python interface.py
```

Los ejemplos usan el puerto que publica el orquestador por defecto. Si cambiaste el puerto o usás un gateway remoto, ajustá la URL.

## Configuración

La GUI lee las variables del entorno de la terminal al arrancar. [`.env.example`](.env.example) sirve de referencia para configurar la autenticación, la CA interna y los tiempos de espera. Si usás el archivo como plantilla, cargá las variables en la terminal antes de iniciar la aplicación.

Para conectarte directamente a los servicios, definí sus URL completas en `PDF_EXTRACT_DOCUMENTS_URL` y `PDF_EXTRACT_EXTRACT_URL`. HTTP está permitido en `localhost`, `127.0.0.1` y `::1`. Para hosts remotos se requiere HTTPS.

## Pruebas

```bash
uv run pytest -q
```

Las pruebas del cliente HTTP usan respuestas simuladas para cubrir casos como el seguimiento por SHA-256 y los reintentos.

## Repositorios del proyecto

- El [orquestador](https://github.com/LautiRebeco/pdf-extract-orchestrator) levanta los servicios y configura Traefik.
- El [servicio de documentos](https://github.com/LautiRebeco/pdf-documents-service) guarda los PDF y gestiona el historial.
- El [servicio de extracción](https://github.com/LautiRebeco/pdf-extract-service) extrae el texto de los PDF.
- La [GUI](https://github.com/LautiRebeco/pdf-gui) es la aplicación de escritorio.
