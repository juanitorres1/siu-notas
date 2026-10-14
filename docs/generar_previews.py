"""Genera las imágenes de ejemplo del README con datos FICTICIOS (no usar datos reales:
docs/ se sube al repo). Uso: <python del .venv> docs/generar_previews.py"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from playwright.sync_api import sync_playwright  # noqa: E402

import mail_ui  # noqa: E402
import mail_ui_siu  # noqa: E402
from siu_notas import INICIO, SIU, TITULOS, plataforma  # noqa: E402

DOCS = Path(__file__).resolve().parent

NOTAS = {
    "en_curso": [
        "Sistemas Operativos (DEMO101): Parcial - Primer Parcial - 8.50 (Ocho con 50/100) Aprobado 15/09/2026",
        "Redes (DEMO102): Trabajo Práctico - TP Integrador - 6.00 (Seis) Aprobado 02/10/2026",
    ],
    "historia": [
        "Base de Datos (DEMO201): Examen - 9.00 (Nueve) Aprobado 20/07/2026",
        "Física II (DEMO202): Examen - 2.00 (Dos) Desaprobado 22/07/2026",
    ],
}
CREDITOS = [
    {
        "materia": "Actividad Cultural 1.1 (DEMOVU1)",
        "comision": "Taller de Fotografía - Grado Anual - 2026",
        "creditos": "1",
        "asistencia": "80",
        "cuando": "Martes 20/10 y 27/10 de 18:00 a 20:00 h",
        "cupo": 30,
        "inscriptos": 24,
        "quedan": 6,
        "inscripto": False,
    }
]

FALTAS = [
    {
        "materia": "Sistemas Operativos (DEMO101) · Sistemas Operativos Inf",
        "antes": "2",
        "ahora": "3",
        "cambios": [{"accion": "nueva", "fecha": "13/10/2026", "tipo": "Teórico-Práctica",
                     "detalle": "+1 (justificada: No)"}],
    },
    {
        "materia": "Redes (DEMO102)",
        "antes": "1",
        "ahora": "1",
        "cambios": [{"accion": "modificada", "fecha": "06/10/2026", "tipo": "Práctica",
                     "detalle": "justificada No → Si"}],
    },
]

EXAMENES = [
    {
        "materia": "Sistemas Operativos (DEMO101)", "fecha": "02/12/2026 09:00", "turno": "Turno Diciembre",
        "mesa": "Sistemas Operativos", "llamado": "Llamado 1", "tipo": "Regular", "ubicacion": "Sede Pilar",
        "lugar": "Aula 12", "docentes": "Docente Ejemplo (Presidente)", "inscripto": False,
    },
]

with sync_playwright() as p:
    navegador = p.chromium.launch(channel=plataforma().canal_navegador)
    for nombre, modulo in (("mail-siu", mail_ui_siu), ("mail-futurista", mail_ui)):
        html = DOCS / f"{nombre}.html"
        html.write_text(
            modulo.html_novedades(NOTAS, TITULOS, INICIO, CREDITOS, f"{SIU}/cursada", FALTAS, EXAMENES),
            encoding="utf-8",
        )
        pagina = navegador.new_page(viewport={"width": 680, "height": 600})
        pagina.goto(html.as_uri())
        pagina.wait_for_load_state("networkidle")
        pagina.screenshot(path=str(DOCS / f"{nombre}.png"), full_page=True)
        html.unlink()
    navegador.close()
print("OK:", ", ".join(str(x.name) for x in DOCS.glob("*.png")))
