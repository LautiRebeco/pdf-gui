"""Procesos administrativos de una sola ejecución (factor XII)."""

from __future__ import annotations

import json

from scripts.admin import main


def test_check_config_sin_secretos(capsys, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACT_GATEWAY_URL", "https://gateway.local")
    monkeypatch.setenv("PDF_EXTRACT_AUTH_TOKEN", "secreto")
    codigo = main(["check-config"])
    salida = capsys.readouterr().out
    datos = json.loads(salida)
    assert codigo == 0
    assert datos["PDF_EXTRACT_GATEWAY_URL"] == "https://gateway.local"
    assert "secreto" not in salida


def test_config_invalida_devuelve_codigo_2(capsys, monkeypatch):
    monkeypatch.setenv("PDF_EXTRACT_GATEWAY_URL", "http://remoto.local")
    assert main(["check-config"]) == 2
    assert "Configuración inválida" in capsys.readouterr().err


def test_delete_requiere_confirmacion(monkeypatch, capsys):
    monkeypatch.setenv("PDF_EXTRACT_GATEWAY_URL", "https://gateway.local")
    assert main(["delete", "--id", "abc"]) == 2
    assert "--yes" in capsys.readouterr().err
