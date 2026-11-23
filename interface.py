"""Aplicación de escritorio cliente del poly-repo de PDF Extractext.

La configuración NO se edita ni se persiste en la interfaz: se lee del
entorno al arrancar (factor III). La pestaña de Configuración muestra la
configuración efectiva, sin secretos, para que el orquestador pueda
diagnosticar el proceso.
"""

from __future__ import annotations

import logging
import queue
import signal
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from api_client import ApiClient, ApiError
from app_logging import configure_logging, request_context
from app_settings import ConfigError, Settings, load_settings
from health import HealthServer

logger = logging.getLogger(__name__)
PAGE_SIZE = 20

ENV_TEMPLATE = """# Copiar en el entorno del proceso (poly-repo con Traefik)
export PDF_EXTRACT_GATEWAY_URL=https://gateway.local
# export PDF_EXTRACT_DOCUMENTS_URL=https://gateway.local/documents
# export PDF_EXTRACT_EXTRACT_URL=https://gateway.local/extract
# export PDF_EXTRACT_AUTH_TOKEN=<token>
# export PDF_EXTRACT_CA_BUNDLE=/ruta/ca-interna.pem
"""


class PDFExtractApp(ctk.CTk):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.settings = settings
        ctk.set_appearance_mode(settings.theme)
        ctk.set_default_color_theme("blue")
        self.title(f"PDF Extractext · {settings.version}")
        self.geometry("1180x760")
        self.minsize(900, 620)
        self.selected_file: str | None = None
        self.result_source: str | None = None
        self.result_format: str | None = None
        self.busy = False
        self.jobs: queue.Queue[tuple[bool, object, object, str]] = queue.Queue()
        self.history_offset = 0
        self.history_items: list[dict] = []
        self.selected_id: str | None = None
        self.selected_name: str | None = None
        self._closing = False

        self.client = ApiClient(settings)
        self.executor = ThreadPoolExecutor(
            max_workers=settings.max_workers,
            thread_name_prefix="pdf-gui",
        )
        self.health = HealthServer(settings)

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._build_layout()
        self.show_page("extract")
        self.after(80, self._poll_jobs)
        self.health.start()

    # ------------------------------------------------------------------ API

    def api(self) -> ApiClient:
        """Un único cliente por proceso: reutiliza el pool de conexiones."""

        return self.client

    # --------------------------------------------------------------- Layout

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        side = ctk.CTkFrame(self, width=210, corner_radius=0, fg_color=("#eef2f7", "#151a24"))
        side.grid(row=0, column=0, sticky="nsew")
        side.grid_propagate(False)
        ctk.CTkLabel(
            side, text="PDF", font=ctk.CTkFont(size=28, weight="bold"), text_color="#3b82f6"
        ).pack(anchor="w", padx=24, pady=(32, 0))
        ctk.CTkLabel(side, text="EXTRACTEXT", font=ctk.CTkFont(size=13, weight="bold")).pack(
            anchor="w", padx=24
        )
        ctk.CTkLabel(
            side, text="Tu espacio de documentos", text_color="gray", font=ctk.CTkFont(size=11)
        ).pack(anchor="w", padx=24, pady=(3, 36))
        self.nav = {}
        for key, label in [
            ("extract", "  Extraer texto"),
            ("history", "  Historial"),
            ("settings", "  Configuración"),
        ]:
            button = ctk.CTkButton(
                side,
                text=label,
                anchor="w",
                height=42,
                corner_radius=9,
                fg_color="transparent",
                text_color=("#273449", "#e4eaf3"),
                hover_color=("#dbeafe", "#23324a"),
                command=lambda k=key: self.show_page(k),
            )
            button.pack(fill="x", padx=14, pady=3)
            self.nav[key] = button
        ctk.CTkLabel(
            side,
            text=f"v{self.settings.version}\nCliente Traefik / poly-repo",
            justify="left",
            text_color="gray",
            font=ctk.CTkFont(size=10),
        ).pack(side="bottom", anchor="w", padx=24, pady=24)
        self.content = ctk.CTkFrame(self, corner_radius=0, fg_color="transparent")
        self.content.grid(row=0, column=1, sticky="nsew")
        self.content.grid_rowconfigure(0, weight=1)
        self.content.grid_columnconfigure(0, weight=1)
        self.pages = {}
        for name in ("extract", "history", "settings"):
            page = ctk.CTkFrame(self.content, fg_color="transparent")
            page.grid(row=0, column=0, sticky="nsew", padx=32, pady=25)
            self.pages[name] = page
        self._build_extract()
        self._build_history()
        self._build_settings()
        self.status = ctk.CTkLabel(
            self.content,
            text="Listo para comenzar",
            anchor="w",
            text_color="gray",
            font=ctk.CTkFont(size=11),
        )
        self.status.grid(row=1, column=0, sticky="ew", padx=34, pady=(0, 10))

    def _heading(self, page: ctk.CTkFrame, title: str, subtitle: str) -> None:
        ctk.CTkLabel(page, text=title, font=ctk.CTkFont(size=26, weight="bold"), anchor="w").pack(
            fill="x", anchor="w"
        )
        ctk.CTkLabel(page, text=subtitle, text_color="gray", anchor="w").pack(
            fill="x", anchor="w", pady=(2, 18)
        )

    def _build_extract(self) -> None:
        page = self.pages["extract"]
        self._heading(
            page, "Extraer texto", "Convertí tus archivos PDF en contenido listo para usar."
        )
        card = ctk.CTkFrame(page, corner_radius=12)
        card.pack(fill="x", pady=(0, 12))
        top = ctk.CTkFrame(card, fg_color="transparent")
        top.pack(fill="x", padx=18, pady=(15, 5))
        info = ctk.CTkFrame(top, fg_color="transparent")
        info.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            info, text="DOCUMENTO", text_color="gray", font=ctk.CTkFont(size=10, weight="bold")
        ).pack(anchor="w")
        self.file_label = ctk.CTkLabel(
            info,
            text="Ningún archivo seleccionado",
            font=ctk.CTkFont(size=15, weight="bold"),
            anchor="w",
        )
        self.file_label.pack(fill="x", anchor="w")
        self.file_info = ctk.CTkLabel(
            info, text="Seleccioná un archivo PDF para comenzar", text_color="gray", anchor="w"
        )
        self.file_info.pack(fill="x", anchor="w")
        self.select_button = ctk.CTkButton(
            top, text="Seleccionar PDF", width=140, command=self.select_file
        )
        self.select_button.pack(side="right", padx=(12, 0))
        actions = ctk.CTkFrame(card, fg_color="transparent")
        actions.pack(fill="x", padx=18, pady=(8, 14))
        ctk.CTkLabel(actions, text="Formato:", text_color="gray").pack(side="left", padx=(0, 10))
        self.format_choice = ctk.CTkSegmentedButton(actions, values=["Texto", "Markdown"])
        self.format_choice.set("Texto")
        self.format_choice.pack(side="left")
        self.extract_button = ctk.CTkButton(
            actions, text="Iniciar extracción", height=36, command=self.extract
        )
        self.extract_button.pack(side="right")
        self.progress = ctk.CTkProgressBar(page, mode="determinate", height=6)
        self.progress.pack(fill="x", pady=(0, 10))
        self.progress.set(0)
        result_head = ctk.CTkFrame(page, fg_color="transparent")
        result_head.pack(fill="x", pady=(3, 5))
        ctk.CTkLabel(result_head, text="Resultado", font=ctk.CTkFont(size=17, weight="bold")).pack(
            side="left"
        )
        self.result_label = ctk.CTkLabel(result_head, text="Sin contenido", text_color="gray")
        self.result_label.pack(side="left", padx=12)
        ctk.CTkButton(
            result_head,
            text="Copiar",
            width=90,
            fg_color="transparent",
            border_width=1,
            command=self.copy_result,
        ).pack(side="right", padx=(8, 0))
        ctk.CTkButton(result_head, text="Exportar", width=100, command=self.export_result).pack(
            side="right"
        )
        self.textbox = ctk.CTkTextbox(
            page, wrap="word", corner_radius=10, font=ctk.CTkFont(size=13)
        )
        self.textbox.pack(fill="both", expand=True)
        ctk.CTkLabel(
            page,
            text="Podés editar el resultado antes de copiarlo o exportarlo.",
            text_color="gray",
            font=ctk.CTkFont(size=11),
        ).pack(anchor="w", pady=(6, 0))

    def _build_history(self) -> None:
        page = self.pages["history"]
        self._heading(
            page, "Historial", "Consultá los documentos guardados por el servicio de texto."
        )
        bar = ctk.CTkFrame(page, fg_color="transparent")
        bar.pack(fill="x", pady=(0, 10))
        ctk.CTkButton(bar, text="Actualizar", width=100, command=lambda: self.load_history(0)).pack(
            side="left"
        )
        self.history_page_label = ctk.CTkLabel(bar, text="", text_color="gray")
        self.history_page_label.pack(side="right")
        self.history_frame = ctk.CTkScrollableFrame(page, corner_radius=10)
        self.history_frame.pack(fill="both", expand=True)
        controls = ctk.CTkFrame(page, fg_color="transparent")
        controls.pack(fill="x", pady=(12, 0))
        ctk.CTkButton(
            controls,
            text="Anterior",
            width=85,
            command=lambda: self.load_history(max(0, self.history_offset - PAGE_SIZE)),
        ).pack(side="left", padx=(0, 6))
        ctk.CTkButton(controls, text="Siguiente", width=85, command=self.next_history).pack(
            side="left"
        )
        ctk.CTkButton(
            controls,
            text="Eliminar",
            width=90,
            fg_color="#b94a48",
            hover_color="#963c3b",
            command=self.delete_history,
        ).pack(side="right", padx=(6, 0))
        ctk.CTkButton(
            controls,
            text="Renombrar",
            width=100,
            fg_color="transparent",
            border_width=1,
            command=self.rename_history,
        ).pack(side="right", padx=6)
        ctk.CTkButton(controls, text="Abrir texto", width=110, command=self.open_history).pack(
            side="right"
        )
        self.selection_label = ctk.CTkLabel(
            page, text="Seleccioná un documento de la lista.", text_color="gray", anchor="w"
        )
        self.selection_label.pack(fill="x", pady=(8, 0))

    def _build_settings(self) -> None:
        page = self.pages["settings"]
        self._heading(
            page,
            "Configuración efectiva",
            "Configurada por variables de entorno (12-factor). No se guarda nada en disco.",
        )
        box = ctk.CTkFrame(page, corner_radius=12)
        box.pack(fill="both", expand=True)
        for key, value in self.settings.public_dict().items():
            row = ctk.CTkFrame(box, fg_color="transparent")
            row.pack(fill="x", padx=20, pady=3)
            ctk.CTkLabel(
                row,
                text=key,
                font=ctk.CTkFont(size=11, weight="bold"),
                anchor="w",
                width=300,
            ).pack(side="left")
            ctk.CTkLabel(row, text=str(value), anchor="w", wraplength=520).pack(side="left")
        ctk.CTkButton(
            box,
            text="Copiar plantilla de entorno",
            command=self.copy_environment,
        ).pack(anchor="w", padx=20, pady=18)
        ctk.CTkLabel(
            box,
            text="Para cambiar estos valores, exportá las variables antes de iniciar la app. "
            "Los secretos nunca se muestran ni se persisten.",
            text_color="gray",
            wraplength=700,
            justify="left",
        ).pack(anchor="w", padx=20, pady=(0, 18))

    def copy_environment(self) -> None:
        self.clipboard_clear()
        self.clipboard_append(ENV_TEMPLATE)
        self.status.configure(text="Plantilla de entorno copiada")

    # ------------------------------------------------------------ Navegación

    def show_page(self, name: str) -> None:
        self.pages[name].tkraise()
        for key, button in self.nav.items():
            button.configure(fg_color=("#dbeafe", "#263b59") if key == name else "transparent")
        if name == "history" and not self.busy:
            self.load_history(0)

    def select_file(self) -> None:
        if self.busy:
            return
        path = filedialog.askopenfilename(
            title="Seleccionar PDF", filetypes=[("Archivos PDF", "*.pdf")]
        )
        if not path:
            return
        try:
            size = Path(path).stat().st_size
        except OSError as exc:
            messagebox.showerror("Archivo", f"No se pudo acceder al archivo: {exc}", parent=self)
            return
        self.selected_file = path
        self.file_label.configure(text=Path(path).name)
        self.file_info.configure(text=f"{size / 1024 / 1024:.2f} MB · {path}")
        self.textbox.delete("1.0", "end")
        self.result_source = self.result_format = None
        self.result_label.configure(text="Sin contenido")

    # ---------------------------------------------------------- Trabajo/IO

    def run_job(self, label: str, work: object, on_success: object) -> None:
        if self.busy:
            messagebox.showinfo(
                "Operación en curso", "Esperá a que termine la operación actual.", parent=self
            )
            return
        self.busy = True
        self.select_button.configure(state="disabled")
        self.extract_button.configure(state="disabled")
        self.progress.configure(mode="indeterminate")
        self.progress.start()
        self.status.configure(text=label)

        def worker() -> None:
            with request_context() as request_id:
                try:
                    result = work()  # type: ignore[operator]
                    self.jobs.put((True, result, on_success, request_id))
                except Exception as exc:  # noqa: BLE001 - límite del hilo
                    logger.exception("job.failed")
                    self.jobs.put((False, exc, on_success, request_id))

        self.executor.submit(worker)

    def _poll_jobs(self) -> None:
        try:
            while True:
                success, result, callback, request_id = self.jobs.get_nowait()
                self.busy = False
                self.select_button.configure(state="normal")
                self.extract_button.configure(state="normal")
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self.progress.set(0)
                if success:
                    self.status.configure(text=f"Operación completada · req={request_id[:8]}")
                    callback(result)  # type: ignore[operator]
                else:
                    self.status.configure(text="No se pudo completar la operación")
                    if self.result_source is None:
                        self.result_label.configure(text="Sin contenido")
                    messagebox.showerror(
                        "Error",
                        (
                            str(result)
                            if isinstance(result, ApiError)
                            else "Ocurrió un error inesperado. Revisá los registros."
                        ),
                        parent=self,
                    )
        except queue.Empty:
            pass
        except Exception:
            logger.exception("poll.failed")
        finally:
            # Se reprograma incluso si un callback explotó: sin esto la UI
            # deja de procesar trabajos silenciosamente.
            if not self._closing:
                self.after(80, self._poll_jobs)

    # ------------------------------------------------------------ Acciones

    def extract(self) -> None:
        if not self.selected_file:
            messagebox.showwarning(
                "Seleccionar PDF", "Seleccioná un archivo PDF primero.", parent=self
            )
            return
        if self.busy:
            return
        path = self.selected_file
        fmt = "md" if self.format_choice.get() == "Markdown" else "txt"
        self.textbox.delete("1.0", "end")
        self.result_source = self.result_format = None
        self.result_label.configure(text="Procesando…")
        client = self.api()

        def done(text: str) -> None:
            self.textbox.delete("1.0", "end")
            self.textbox.insert("1.0", text)
            self.result_source, self.result_format = Path(path).name, fmt
            self.result_label.configure(text=f"{self.result_source} · {fmt.upper()}")
            if not text.strip():
                messagebox.showinfo(
                    "Sin texto",
                    "No se encontró texto. Si el documento es escaneado, el OCR debe estar "
                    "habilitado en el servidor.",
                    parent=self,
                )

        self.run_job(
            "Extrayendo contenido… La barra indica actividad, no porcentaje.",
            lambda: client.extract(path, fmt),
            done,
        )

    def current_text(self) -> str:
        return self.textbox.get("1.0", "end-1c")

    def copy_result(self) -> None:
        text = self.current_text()
        if not text:
            messagebox.showwarning("Sin contenido", "No hay texto para copiar.", parent=self)
            return
        self.clipboard_clear()
        self.clipboard_append(text)
        self.status.configure(text="Texto copiado al portapapeles")

    def export_result(self) -> None:
        text = self.current_text()
        if not text:
            messagebox.showwarning("Sin contenido", "No hay texto para exportar.", parent=self)
            return
        ext = "." + (self.result_format or "txt")
        name = Path(self.result_source or "resultado").stem + ext
        path = filedialog.asksaveasfilename(
            title="Exportar resultado",
            initialfile=name,
            defaultextension=ext,
            filetypes=[("Texto", "*.txt"), ("Markdown", "*.md")],
        )
        if path:
            try:
                Path(path).write_text(text, encoding="utf-8")
            except OSError as exc:
                messagebox.showerror("Error al guardar", str(exc), parent=self)
                return
            self.status.configure(text="Resultado guardado correctamente")

    def load_history(self, offset: int) -> None:
        client = self.api()

        def done(items: list[dict]) -> None:
            self.history_offset, self.history_items = offset, items
            self.selected_id = self.selected_name = None
            self.selection_label.configure(text="Seleccioná un documento de la lista.")
            for widget in self.history_frame.winfo_children():
                widget.destroy()
            if not items:
                ctk.CTkLabel(
                    self.history_frame, text="No hay documentos en esta página.", text_color="gray"
                ).pack(pady=24)
            for doc in items:
                doc_id = str(doc.get("_id", ""))
                name = str(doc.get("pdf_nombre", "Sin nombre"))
                date = str(doc.get("created_at", ""))[:16].replace("T", " ")
                label = f'{name}   ·   {doc.get("estado", "")}   ·   {date}'
                ctk.CTkButton(
                    self.history_frame,
                    text=label,
                    anchor="w",
                    fg_color="transparent",
                    text_color=("#273449", "#e4eaf3"),
                    hover_color=("#dbeafe", "#23324a"),
                    command=lambda i=doc_id, n=name: self.select_history(i, n),
                ).pack(fill="x", padx=6, pady=3)
            self.history_page_label.configure(
                text=f"Documentos {offset + 1 if items else 0}–{offset + len(items)}"
            )

        self.run_job("Cargando historial…", lambda: client.history(offset, PAGE_SIZE), done)

    def next_history(self) -> None:
        if len(self.history_items) == PAGE_SIZE:
            self.load_history(self.history_offset + PAGE_SIZE)

    def select_history(self, doc_id: str, name: str) -> None:
        self.selected_id, self.selected_name = doc_id, name
        self.selection_label.configure(text=f"Seleccionado: {name}")

    def _has_selection(self) -> bool:
        if not self.selected_id:
            messagebox.showwarning("Historial", "Seleccioná un documento de la lista.", parent=self)
            return False
        return True

    def open_history(self) -> None:
        if not self._has_selection():
            return
        client, doc_id = self.api(), self.selected_id

        def done(doc: dict) -> None:
            self.textbox.delete("1.0", "end")
            self.textbox.insert("1.0", doc["txt_contenido"])
            self.result_source, self.result_format = doc.get("pdf_nombre", "documento"), "txt"
            self.result_label.configure(text=f"{self.result_source} · TXT · Historial")
            self.show_page("extract")

        self.run_job("Abriendo documento…", lambda: client.document(doc_id), done)

    def rename_history(self) -> None:
        if not self._has_selection():
            return
        dialog = ctk.CTkInputDialog(title="Renombrar", text="Nuevo nombre del documento:")
        name = dialog.get_input()
        if name is None:
            return
        name = name.strip()
        if not 1 <= len(name) <= 200:
            messagebox.showwarning(
                "Nombre inválido", "Ingresá un nombre de entre 1 y 200 caracteres.", parent=self
            )
            return
        client, doc_id, offset = self.api(), self.selected_id, self.history_offset
        self.run_job(
            "Renombrando documento…",
            lambda: client.rename(doc_id, name),
            lambda _: self.load_history(offset),
        )

    def delete_history(self) -> None:
        if not self._has_selection():
            return
        if not messagebox.askyesno(
            "Eliminar", f"¿Eliminar «{self.selected_name}» del historial?", parent=self
        ):
            return
        client, doc_id, offset = self.api(), self.selected_id, self.history_offset
        self.run_job(
            "Eliminando documento…",
            lambda: client.delete(doc_id),
            lambda _: self.load_history(offset),
        )

    # ------------------------------------------------------------ Ciclo de vida

    def on_close(self) -> None:
        """Apagado ordenado: cancelar trabajos, cerrar pool y quitar health."""

        if self._closing:
            return
        self._closing = True
        logger.info("app.shutdown", extra={"event": "app.shutdown"})
        self.health.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.client.close()
        self.destroy()


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuración inválida: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    configure_logging(settings)
    app = PDFExtractApp(settings)

    def handle_signal(signum: int, _frame: object) -> None:
        logger.warning("app.signal signal=%d", signum, extra={"event": "app.signal"})
        app.after(0, app.on_close)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle_signal)
        except ValueError:
            # Señales solo pueden registrarse en el hilo principal.
            pass

    app.mainloop()


if __name__ == "__main__":
    main()
