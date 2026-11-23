"""Pruebas de la UI sin abrir ventana real."""

from __future__ import annotations

import queue
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

import interface


def crear_interfaz_simulada(texto="Contenido editado"):
    return SimpleNamespace(
        textbox=Mock(),
        current_text=Mock(return_value=texto),
        status=Mock(),
        clipboard_clear=Mock(),
        clipboard_append=Mock(),
        result_source="ejemplo.pdf",
        result_format="txt",
    )


def test_copiar_usa_el_texto_visible():
    app = crear_interfaz_simulada()
    interface.PDFExtractApp.copy_result(app)
    app.clipboard_append.assert_called_once_with("Contenido editado")


def test_exportar_guarda_el_texto_editado(tmp_path, monkeypatch):
    app = crear_interfaz_simulada("Texto corregido: áé")
    archivo = tmp_path / "resultado.txt"
    monkeypatch.setattr(interface.filedialog, "asksaveasfilename", lambda **kwargs: str(archivo))
    interface.PDFExtractApp.export_result(app)
    assert archivo.read_text(encoding="utf-8") == "Texto corregido: áé"


def test_la_configuracion_no_se_guarda_en_disco():
    assert not hasattr(interface.PDFExtractApp, "apply_settings")
    assert hasattr(interface.PDFExtractApp, "copy_environment")


def test_seleccionar_otro_pdf_limpia_resultado_anterior(tmp_path, monkeypatch):
    pdf = tmp_path / "nuevo.pdf"
    pdf.write_bytes(b"%PDF-ejemplo")
    monkeypatch.setattr(interface.filedialog, "askopenfilename", lambda **kwargs: str(pdf))
    app = SimpleNamespace(
        busy=False,
        file_label=Mock(),
        file_info=Mock(),
        textbox=Mock(),
        result_source="anterior.pdf",
        result_format="txt",
        result_label=Mock(),
    )
    interface.PDFExtractApp.select_file(app)
    assert app.selected_file == str(pdf)
    assert app.result_source is None
    assert app.result_format is None


def test_extraccion_markdown_solicita_formato_correcto(tmp_path):
    app = SimpleNamespace(
        selected_file=str(tmp_path / "prueba.pdf"),
        busy=False,
        format_choice=Mock(),
        textbox=Mock(),
        result_label=Mock(),
        api=Mock(),
        run_job=Mock(),
    )
    app.format_choice.get.return_value = "Markdown"
    interface.PDFExtractApp.extract(app)
    trabajo = app.run_job.call_args.args[1]
    trabajo()
    app.api.return_value.extract.assert_called_once_with(app.selected_file, "md")
    assert app.result_source is None


def test_tarea_en_segundo_plano_devuelve_request_id():
    executor = ThreadPoolExecutor(max_workers=1)
    app = SimpleNamespace(
        busy=False,
        select_button=Mock(),
        extract_button=Mock(),
        progress=Mock(),
        status=Mock(),
        jobs=queue.Queue(),
        executor=executor,
    )
    callback = Mock()
    try:
        interface.PDFExtractApp.run_job(app, "Procesando", lambda: "resultado", callback)
        exito, resultado, callback_recibido, request_id = app.jobs.get(timeout=2)
        assert exito is True
        assert resultado == "resultado"
        assert callback_recibido is callback
        assert len(request_id) == 32
        assert app.busy is True
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def test_poll_se_reprograma_aunque_un_callback_falle():
    app = SimpleNamespace(
        busy=True,
        select_button=Mock(),
        extract_button=Mock(),
        progress=Mock(),
        status=Mock(),
        jobs=queue.Queue(),
        after=Mock(),
        _closing=False,
        result_source=None,
        result_label=Mock(),
        _poll_jobs=interface.PDFExtractApp._poll_jobs,
    )

    def explota(_resultado):
        raise RuntimeError("callback roto")

    app.jobs.put((True, "valor", explota, "abc"))
    interface.PDFExtractApp._poll_jobs(app)
    app.after.assert_called_once()
    assert app.after.call_args.args[1] is interface.PDFExtractApp._poll_jobs


def test_on_close_es_idempotente():
    app = SimpleNamespace(
        _closing=False,
        health=Mock(),
        executor=Mock(),
        client=Mock(),
        destroy=Mock(),
    )
    interface.PDFExtractApp.on_close(app)
    interface.PDFExtractApp.on_close(app)
    app.health.stop.assert_called_once()
    app.executor.shutdown.assert_called_once()
    app.client.close.assert_called_once()
    app.destroy.assert_called_once()


def test_historial_no_avanza_si_la_pagina_esta_incompleta():
    app = SimpleNamespace(history_items=[{}], history_offset=0, load_history=Mock())
    interface.PDFExtractApp.next_history(app)
    app.load_history.assert_not_called()


def test_historial_avanza_si_la_pagina_esta_completa():
    app = SimpleNamespace(
        history_items=[{}] * interface.PAGE_SIZE,
        history_offset=20,
        load_history=Mock(),
    )
    interface.PDFExtractApp.next_history(app)
    app.load_history.assert_called_once_with(40)
