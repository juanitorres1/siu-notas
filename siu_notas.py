"""Avisa por mail cuando aparecen notas nuevas en el SIU Guaraní de la Austral.

El login es por Microsoft (SAML). Con Edge y la cuenta de la Austral vinculada a
Windows, el SSO entra solo; si no, se hace una vez a mano y queda en ./perfil.
Lo que depende del sistema operativo (navegador, venv, tarea programada) vive en
el paquete `plataformas`.
  - `python siu_notas.py check`        -> headless: elige la carrera y lee parciales,
     Actuación provisoria, Historia académica y actividades con créditos; compara
     contra lo ya visto y manda un mail si hay novedades.
  - `python siu_notas.py login`        -> abre el navegador para loguearte a mano (MFA).
  - `python siu_notas.py dump`         -> guarda lo leído en ./capturas/filas.json.
  - `python siu_notas.py test-mail`    -> prueba la config SMTP.
  - `python siu_notas.py verificar`    -> chequea los requisitos del sistema.
  - `python siu_notas.py programar`    -> crea la ejecución periódica (SIU_INTERVALO_MIN).
  - `python siu_notas.py desprogramar` -> la elimina.
"""

import hashlib
import json
import os
import re
import smtplib
import sys
import time
import traceback
import unicodedata
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.sync_api import BrowserContext, Page, sync_playwright

import mail_ui as mail_ui_futurista
import mail_ui_siu
import plataformas

BASE = Path(__file__).parent
PERFIL = BASE / "perfil"
ESTADO = BASE / "estado" / "snapshot.json"
CAPTURAS = BASE / "capturas"

SIU = "https://siu.austral.edu.ar/portal"
INICIO = f"{SIU}/inicio_alumno"
HISTORIA = f"{SIU}/historia_academica"
ACTUACION = f"{SIU}/actuacion_provisoria"

TITULOS = {
    "en_curso": "Materias en curso (parciales)",
    "actuacion": "Actuación provisoria (finales)",
    "historia": "Historia académica (nota definitiva)",
}

NOMBRE_TAREA = "SIU notas"

LOG = BASE / "estado" / "siu.log"
LOG_MAX = 1_000_000  # bytes; al pasarlo se queda con la segunda mitad

load_dotenv(BASE / ".env")
# Con pythonw (tarea programada) no hay consola: sys.stdout es None.
if sys.stdout:
    sys.stdout.reconfigure(encoding="utf-8")

# Diseño del mail: "siu" (formal, tipo Guaraní) o "futurista".
mail_ui = mail_ui_futurista if os.getenv("MAIL_TEMA", "siu").lower() == "futurista" else mail_ui_siu

# Propuesta a seleccionar arriba a la derecha (por defecto el SIU abre "Aspirantes").
CARRERA = os.getenv("SIU_CARRERA", "").strip()


def normalizar(texto: str) -> str:
    sin_tildes = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sin_tildes.lower().split())


def limpiar(texto: str) -> str:
    return re.sub(r"\s*-\s*Detalle$", "", " ".join(texto.split()))


def log(msg: str) -> None:
    linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    if sys.stdout:
        print(linea, flush=True)
    LOG.parent.mkdir(exist_ok=True)
    if LOG.exists() and LOG.stat().st_size > LOG_MAX:
        texto = LOG.read_text(encoding="utf-8", errors="ignore")
        LOG.write_text(texto[len(texto) // 2:], encoding="utf-8")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(linea + "\n")


_plataforma: plataformas.Plataforma | None = None


def plataforma() -> plataformas.Plataforma:
    """La plataforma del sistema actual (se resuelve la primera vez que se usa)."""
    global _plataforma
    if _plataforma is None:
        _plataforma = plataformas.actual(BASE)
    return _plataforma


# ---------- navegador ----------

def abrir_contexto(p, headless: bool) -> BrowserContext:
    PERFIL.mkdir(exist_ok=True)
    canal = os.getenv("SIU_NAVEGADOR") or plataforma().canal_navegador
    return p.chromium.launch_persistent_context(
        str(PERFIL), channel=canal, headless=headless, locale="es-AR", viewport={"width": 1280, "height": 900}
    )


def en_siu(url: str) -> bool:
    return urlparse(url).hostname == "siu.austral.edu.ar"


def esta_logueado_url(url: str) -> bool:
    return en_siu(url) and "/acceso" not in urlparse(url).path


def esta_logueado(page: Page) -> bool:
    return esta_logueado_url(page.url)


def ir_al_inicio(page: Page) -> bool:
    """Entra al inicio de alumno. Si la sesión del SIU venció, dispara el SSO de
    Microsoft, que vuelve solo si la sesión de Microsoft sigue viva."""
    page.goto(INICIO, wait_until="networkidle")
    if esta_logueado(page):
        return True
    # El login de Microsoft casi nunca llega a "networkidle" (telemetría, polling):
    # alcanza con que cargue el DOM; lo que importa es si vuelve solo al SIU.
    page.goto(f"{SIU}/acceso?auth=saml", wait_until="domcontentloaded")
    try:
        page.wait_for_url(en_siu, timeout=30_000)
        page.wait_for_load_state("networkidle")
    except Exception:
        pass
    return esta_logueado(page)


def elegir_carrera(page: Page) -> None:
    opciones = page.query_selector_all("a.js-select-carreras")
    # Sin SIU_CARRERA o con una sola propuesta (no hay selector): se usa la actual.
    if not CARRERA or not opciones:
        return
    objetivo = normalizar(CARRERA)
    for a in opciones:
        if objetivo in normalizar(a.get_attribute("title") or ""):
            if not a.get_attribute("data-carrera-selected"):
                # Click por JS: el link está dentro de un dropdown cerrado.
                a.evaluate("e => e.click()")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(1500)
            return
    raise RuntimeError(f"No encontré la carrera '{CARRERA}' en el selector de propuestas.")


def leer_historia(page: Page) -> list[str]:
    """Una fila por nota: 'Materia (COD): Examen - 7.00 (Siete) Aprobado 18/07/2024'."""
    page.goto(HISTORIA, wait_until="networkidle")
    page.click("a[consulta='todo']")
    page.wait_for_selector("#listado .catedras", timeout=30_000)
    page.wait_for_load_state("networkidle")
    crudas = page.eval_on_selector_all(
        "#listado .catedras",
        """bloques => bloques.flatMap(b => {
            const materia = b.querySelector('h3')?.innerText.trim() ?? '';
            return [...b.querySelectorAll('.catedra_nombre')].map(n => [materia, n.innerText]);
        })""",
    )
    filas = [f"{materia}: {limpiar(texto)}" for materia, texto in crudas]
    # "En curso - Inicio de dictado ..." no es una nota.
    return list(dict.fromkeys(f for f in filas if ": En curso" not in f))


def leer_en_curso(page: Page) -> tuple[list[str], set[str]]:
    """Notas de parciales/TPs de las materias en curso, y los códigos de esas materias.
    Las notas están en el 'Detalle' de cada materia, que se carga por Ajax
    (POST historia_academica/detalle_encurso).
    Una fila por evaluación: 'Materia (COD): Parcial - Primer Parcial - 9.20 (...) Aprobado 15/09/2026'."""
    page.goto(HISTORIA, wait_until="networkidle")
    page.click("a[consulta='mats_en_curso']")
    page.wait_for_selector("#listado .catedras", timeout=30_000)
    page.wait_for_load_state("networkidle")
    titulos = page.eval_on_selector_all("#listado .catedras h3", "hs => hs.map(h => h.innerText.trim())")
    codigos = {m.group(1) for t in titulos if (m := re.search(r"\(([^()]+)\)\s*$", t))}
    for link in page.query_selector_all("#listado .catedra_nombre a"):
        link.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(500)
    crudas = page.eval_on_selector_all(
        "#listado .catedras",
        """bloques => bloques.flatMap(b => {
            const materia = b.querySelector('h3')?.innerText.trim() ?? '';
            return [...b.querySelectorAll('.toggle_info_catedra tr')]
                .map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()))
                .filter(c => c.length >= 5)
                .map(c => [materia, c]);
        })""",
    )
    filas = []
    for materia, (fecha, descripcion, tipo, nota, resultado, *_) in crudas:
        filas.append(" ".join(f"{materia}: {tipo} - {descripcion} - {nota} {resultado} {fecha}".split()))
    return list(dict.fromkeys(filas)), codigos


def leer_actuacion(page: Page) -> list[str]:
    """Bloques Cursadas / Promociones / Exámenes. Hoy no hay datos para ver la
    estructura con notas, así que toma filas de tabla o, si no hay, líneas de texto."""
    page.goto(ACTUACION, wait_until="networkidle")
    crudas = page.eval_on_selector_all(
        "#cursa, #promo, #exa",
        """bloques => bloques.flatMap(b => {
            const titulo = b.querySelector('h2')?.innerText.trim() ?? b.id;
            const trs = [...b.querySelectorAll('tr')]
                .map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()).join(' | '))
                .filter(t => t.replace(/[\\s|]/g, ''));
            const lineas = trs.length ? trs : [...b.querySelectorAll(':scope > :not(.titulo_operacion_)')]
                .flatMap(e => e.innerText.split('\\n'));
            return lineas.map(l => [titulo, l]);
        })""",
    )
    filas = [f"{titulo}: {limpiar(linea)}" for titulo, linea in crudas if linea.strip()]
    return list(dict.fromkeys(f for f in filas if "no hay informacion" not in normalizar(f)))


DIAS = (
    r"lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|\blun\b|\bmar\b|\bmi[eé]\b|\bjue\b|\bvie\b|\bs[aá]b\b"
    r"|\bde (enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\b"
)


def _info_comision(materia: str, comision: str, cupo_txt: str, obs: str, inscripto: bool) -> dict:
    cupo = re.search(r"(\d+)\s*\|\s*Inscriptos:\s*(\d+)", cupo_txt)
    creditos = re.search(r"(\d+(?:[.,]\d+)?)\s*cr[eé]ditos?", obs, re.I)
    asistencia = re.search(r"asistencia[^\d%]*(\d{1,3})\s*%", obs, re.I)
    lineas = [" ".join(l.split()) for l in obs.splitlines() if l.strip()]
    cuando = [l for l in lineas if re.search(DIAS, l, re.I) or re.search(r"\b\d{1,2}/\d{1,2}\b", l)]
    total, inscriptos = (int(cupo.group(1)), int(cupo.group(2))) if cupo else (None, None)
    return {
        "materia": materia,
        "comision": comision,
        "creditos": creditos.group(1).replace(",", ".") if creditos else "",
        "asistencia": asistencia.group(1) if asistencia else "",
        "cuando": " · ".join(cuando),
        "cupo": total,
        "inscriptos": inscriptos,
        "quedan": None if total is None else max(total - inscriptos, 0),
        "inscripto": inscripto,
        "observaciones": " ".join(lineas),
    }


def leer_creditos(page: Page) -> list[dict]:
    """Comisiones de Inscripción a materias que dan créditos (según 'Observaciones').
    Solo lee: entra a cursada/elegir_materia/<id> (GET) y NUNCA envía el form de inscripción."""
    page.goto(f"{SIU}/cursada", wait_until="networkidle")
    links = page.eval_on_selector_all(
        "a[href*='/cursada/elegir_materia/']", "as => as.map(a => [a.title, a.href])"
    )
    comisiones = []
    for titulo, href in dict(links).items():
        page.goto(href, wait_until="networkidle")
        materia = re.sub(r"\s*-\s*\(", " (", titulo)  # "Ayudantía 6 - (COD)" -> "Ayudantía 6 (COD)"
        crudas = page.eval_on_selector_all(
            "#comisiones li.comision",
            """lis => lis.map(li => {
                const campo = (nombre, crudo = false) => {
                    for (const row of li.querySelectorAll('.row-fluid')) {
                        const k = row.querySelector('.span3');
                        if (k && k.innerText.trim().toLowerCase().startsWith(nombre))
                            { const v = row.querySelector('.span9'); return (crudo ? v?.textContent : v?.innerText) ?? ''; }
                    }
                    return '';
                };
                return [
                    (li.querySelector('h4')?.innerText ?? '').replace(/^\\s*Comisi[oó]n:\\s*/, '').trim(),
                    campo('cupo'), campo('observaciones', true),
                    !li.classList.contains('no-inscripto'),
                ];
            })""",
        )
        for comision, cupo, obs, inscripto in crudas:
            info = _info_comision(materia, comision, cupo, obs, inscripto)
            if info["creditos"]:
                comisiones.append(info)
    return comisiones


def clave_credito(c: dict) -> str:
    return f"{c['materia']} | {c['comision']} | {c['creditos']} créditos"


def texto_credito(c: dict) -> str:
    cupo = f"quedan {c['quedan']} de {c['cupo']}" if c["cupo"] is not None else "cupo s/d"
    partes = [f"{c['creditos']} crédito(s)", cupo]
    if c["asistencia"]:
        partes.append(f"asistencia {c['asistencia']}%")
    if c["cuando"]:
        partes.append(c["cuando"])
    return f"{c['materia']} — {c['comision']}: " + " | ".join(partes)


def leer_inasistencias(page: Page) -> dict[str, dict]:
    """Reportes > Inasistencias. Devuelve {clave: falta}, clave = 'materia | fecha | tipo de clase'.
    Si la página no carga como se espera, lanza excepción: nunca se interpreta como
    "no tenés faltas" (eso haría avisar que se borraron todas)."""
    page.goto(f"{SIU}/reporte_inasistencias", wait_until="networkidle")
    page.wait_for_selector("#inasistencias", state="attached", timeout=30_000)
    crudas = page.eval_on_selector_all(
        "#inasistencias .actividad",
        """bloques => bloques.flatMap(b => {
            const materia = b.querySelector('h3')?.innerText.trim() ?? '';
            return [...b.querySelectorAll('tr')]
                .map(tr => [...tr.querySelectorAll('td')].map(td => td.innerText.trim()))
                .filter(c => c.length >= 5)
                .map(c => [materia, c]);
        })""",
    )
    faltas: dict[str, dict] = {}
    for materia, (fecha, tipo, tema, cantidad, justificada, *_) in crudas:
        # "(GIINDTECOM) Técnicas de Comunicación - Comisión: Comunicación Efectiva Inf"
        #   -> "Técnicas de Comunicación (GIINDTECOM) · Comunicación Efectiva Inf"
        # La comisión importa: suele ser el nombre con el que conocés la materia.
        m = re.match(r"\(([^)]+)\)\s*(.*?)\s*-\s*Comisi[oó]n:\s*(.*)", materia)
        if m:
            codigo, nombre, comision = m.groups()
            nombre = f"{nombre} ({codigo})" + (f" · {comision}" if comision and comision != nombre else "")
        else:
            nombre = materia
        try:
            cant = float(cantidad.replace(",", "."))
        except ValueError:
            cant = 0.0
        falta = {"materia": nombre, "fecha": fecha, "tipo": tipo, "tema": tema,
                 "cantidad": cant, "justificada": justificada}
        clave = f"{nombre} | {fecha} | {tipo}"
        if clave in faltas:  # dos registros iguales el mismo día: se suman
            faltas[clave]["cantidad"] += cant
        else:
            faltas[clave] = falta
    return faltas


def diff_inasistencias(previas: dict[str, dict], actuales: dict[str, dict]) -> list[dict]:
    """Cambios por materia: [{materia, antes, ahora, cambios: [{accion, fecha, tipo, detalle}]}].
    antes/ahora = total de inasistencias de la materia."""
    def total(faltas: dict[str, dict], materia: str) -> float:
        return sum(f["cantidad"] for f in faltas.values() if f["materia"] == materia)

    def fmt(n: float) -> str:
        return f"{n:g}"

    cambios: dict[str, list[dict]] = {}
    for clave in actuales.keys() | previas.keys():
        a, p = actuales.get(clave), previas.get(clave)
        if a and not p:
            item = {"accion": "nueva", "detalle": f"+{fmt(a['cantidad'])} (justificada: {a['justificada']})"}
        elif p and not a:
            item = {"accion": "quitada", "detalle": f"-{fmt(p['cantidad'])}"}
        elif a["cantidad"] != p["cantidad"] or a["justificada"] != p["justificada"]:
            partes = []
            if a["cantidad"] != p["cantidad"]:
                partes.append(f"cantidad {fmt(p['cantidad'])} → {fmt(a['cantidad'])}")
            if a["justificada"] != p["justificada"]:
                partes.append(f"justificada {p['justificada']} → {a['justificada']}")
            item = {"accion": "modificada", "detalle": ", ".join(partes)}
        else:
            continue
        f = a or p
        cambios.setdefault(f["materia"], []).append({**item, "fecha": f["fecha"], "tipo": f["tipo"]})

    def orden_fecha(c: dict) -> str:
        d = c["fecha"].split("/")
        return "/".join(reversed(d)) if len(d) == 3 else c["fecha"]

    return [
        {"materia": materia, "antes": fmt(total(previas, materia)), "ahora": fmt(total(actuales, materia)),
         "cambios": sorted(lista, key=orden_fecha)}
        for materia, lista in sorted(cambios.items())
    ]


def texto_inasistencias(cambio: dict) -> str:
    lineas = [f"{cambio['materia']}: {cambio['antes']} → {cambio['ahora']} inasistencias"]
    for c in cambio["cambios"]:
        lineas.append(f"    - {c['fecha']} ({c['tipo']}): {c['accion']}, {c['detalle']}")
    return "\n".join(lineas)


def leer_examenes(page: Page, codigos: set[str]) -> list[dict]:
    """Mesas de Inscripción a exámenes de las materias en curso (por código).
    Solo lee: entra a examen/elegir_materia/<id> (GET) y NUNCA envía el form de inscripción."""
    page.goto(f"{SIU}/examen", wait_until="networkidle")
    links = page.eval_on_selector_all(
        "a[href*='/examen/elegir_materia/']", "as => as.map(a => [a.title, a.href])"
    )
    mesas = []
    for titulo, href in dict(links).items():
        m = re.search(r"\(([^()]+)\)\s*$", titulo)
        if not m or m.group(1) not in codigos:
            continue
        materia = re.sub(r"\s*-\s*\(", " (", titulo)
        page.goto(href, wait_until="networkidle")
        crudas = page.eval_on_selector_all(
            "#llamados_mesas li.llamado_mesa",
            """lis => lis.map(li => ({
                fecha: li.querySelector('p.fecha')?.innerText.trim() ?? '',
                datos: [...li.querySelectorAll('ul li')].map(x => x.innerText.trim()),
                inscripto: !li.classList.contains('no-inscripto'),
            }))""",
        )
        for c in crudas:
            # "Turno: FI- Mesas Especiales" -> {"turno": "FI- Mesas Especiales"}
            datos = {}
            for linea in c["datos"]:
                clave, _, valor = linea.partition(":")
                valor = " ".join(valor.split())
                datos[normalizar(clave)] = "" if valor == "-" else valor
            mesas.append({
                "materia": materia,
                "fecha": " ".join(c["fecha"].split()),
                "turno": datos.get("turno", ""),
                "mesa": datos.get("mesa", ""),
                "llamado": datos.get("llamado", ""),
                "tipo": datos.get("tipo de inscripcion", ""),
                "ubicacion": datos.get("ubicacion", ""),
                "lugar": datos.get("lugar", ""),
                "docentes": datos.get("docentes", ""),
                "inscripto": c["inscripto"],
            })
    return sorted(mesas, key=lambda x: _fecha_hora(x["fecha"]) or datetime.max)


def _fecha_hora(texto: str) -> datetime | None:
    try:
        return datetime.strptime(texto, "%d/%m/%Y %H:%M")
    except ValueError:
        try:
            return datetime.strptime(texto, "%d/%m/%Y")
        except ValueError:
            return None


def clave_examen(m: dict) -> str:
    return f"{m['materia']} | {m['fecha']} | {m['llamado'] or m['turno']}"


def texto_examen(m: dict) -> str:
    partes = [m["turno"], m["tipo"], " - ".join(x for x in (m["ubicacion"], m["lugar"]) if x), m["docentes"]]
    estado = " [ya inscripto]" if m["inscripto"] else ""
    return f"{m['fecha']} — {m['materia']}{estado}: " + " | ".join(p for p in partes if p)


def ics_examenes(mesas: list[dict], duracion_h: int = 2) -> str:
    """Calendario .ics con una entrada por mesa. El SIU no informa la duración:
    se asume `duracion_h` horas. Horario de Argentina (UTC-3, sin horario de verano)."""
    def esc(t: str) -> str:
        return t.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    def plegar(linea: str) -> str:  # RFC 5545: líneas de hasta 75 octetos
        partes, actual = [], ""
        for ch in linea:
            if len((actual + ch).encode("utf-8")) > 75:
                partes.append(actual)
                actual = " " + ch
            else:
                actual += ch
        return "\r\n".join(partes + [actual])

    ahora = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lineas = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//siu-notas//ES", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    for m in mesas:
        inicio = _fecha_hora(m["fecha"])
        if not inicio:
            continue
        inicio_utc = inicio + timedelta(hours=3)
        fin_utc = inicio_utc + timedelta(hours=duracion_h)
        uid = hashlib.sha1(clave_examen(m).encode("utf-8")).hexdigest()
        descripcion = "\n".join(
            f"{k}: {v}" for k, v in (
                ("Turno", m["turno"]), ("Llamado", m["llamado"]), ("Mesa", m["mesa"]),
                ("Tipo de inscripción", m["tipo"]), ("Docentes", m["docentes"]),
            ) if v
        ) + f"\nDuración estimada ({duracion_h} h): el SIU no la informa.\n{SIU}/examen"
        lineas += [
            "BEGIN:VEVENT",
            f"UID:{uid}@siu-notas",
            f"DTSTAMP:{ahora}",
            f"DTSTART:{inicio_utc:%Y%m%dT%H%M%SZ}",
            f"DTEND:{fin_utc:%Y%m%dT%H%M%SZ}",
            f"SUMMARY:{esc('Examen: ' + m['materia'])}",
            f"LOCATION:{esc(' - '.join(x for x in (m['ubicacion'], m['lugar']) if x))}",
            f"DESCRIPTION:{esc(descripcion)}",
            "END:VEVENT",
        ]
    lineas.append("END:VCALENDAR")
    return "\r\n".join(plegar(l) for l in lineas) + "\r\n"


def leer_todo(page: Page) -> dict:
    """{'notas': {sección: [filas]}, 'creditos': [comisiones], 'inasistencias': {clave: falta},
    'examenes': [mesas de materias en curso]}."""
    elegir_carrera(page)
    en_curso, codigos_en_curso = leer_en_curso(page)
    return {
        "notas": {
            "en_curso": en_curso,
            "actuacion": leer_actuacion(page),
            "historia": leer_historia(page),
        },
        "creditos": leer_creditos(page),
        "inasistencias": leer_inasistencias(page),
        "examenes": leer_examenes(page, codigos_en_curso),
    }


# ---------- estado y mail ----------

def cargar_estado() -> dict:
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    return {}


def guardar_estado(estado: dict) -> None:
    ESTADO.parent.mkdir(exist_ok=True)
    ESTADO.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")


def enviar_mail(
    asunto: str, cuerpo: str, html: str | None = None, adjuntos: list[tuple[str, str, str]] | None = None
) -> None:
    """adjuntos: [(nombre_archivo, contenido_texto, subtipo)] -> text/<subtipo>, ej. ('x.ics', ..., 'calendar')."""
    faltan = [k for k in ("SMTP_USER", "SMTP_PASS", "MAIL_TO") if not os.getenv(k)]
    if faltan:
        log(f"No mando mail, faltan variables en .env: {', '.join(faltan)}")
        log(f"Asunto: {asunto}\n{cuerpo}")
        return
    msg = EmailMessage()
    msg["Subject"] = asunto
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_TO"]
    msg.set_content(cuerpo)
    if html:
        msg.add_alternative(html, subtype="html")
    for nombre, contenido, subtipo in adjuntos or []:
        msg.add_attachment(contenido.encode("utf-8"), maintype="text", subtype=subtipo, filename=nombre)
    host =os.getenv("SMTP_HOST", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(host, port, timeout=30) as s:
        s.login(os.environ["SMTP_USER"], os.environ["SMTP_PASS"])
        s.send_message(msg)
    log(f"Mail enviado: {asunto}")


# ---------- comandos ----------

def cmd_login() -> None:
    with sync_playwright() as p:
        ctx = abrir_contexto(p, headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{SIU}/acceso?auth=saml")
        print("\nLogueate en la ventana (MFA incluido) y elegí 'Mantener la sesión iniciada: Sí'.")
        print("Espero hasta 5 minutos a que llegues al inicio del SIU...\n")
        page.wait_for_url(esta_logueado_url, timeout=300_000)
        page.wait_for_load_state("networkidle")
        if esta_logueado(page):
            log("Sesión guardada en ./perfil. Ya podés correr `check`.")
        else:
            log(f"No parece logueado (url: {page.url}).")
        ctx.close()


def cmd_dump() -> None:
    CAPTURAS.mkdir(exist_ok=True)
    with sync_playwright() as p:
        ctx = abrir_contexto(p, headless=True)
        page = ctx.new_page()
        if not ir_al_inicio(page):
            sys.exit("Sesión vencida: corré `python siu_notas.py login`.")
        lectura = leer_todo(page)
        datos = {**lectura["notas"], **{k: v for k, v in lectura.items() if k != "notas"}}
        (CAPTURAS / "filas.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        ctx.close()
    log(", ".join(f"{k}: {len(v)} filas" for k, v in datos.items()) + f" -> {CAPTURAS}")


def cmd_check() -> None:
    estado = cargar_estado()
    with sync_playwright() as p:
        ctx = abrir_contexto(p, headless=True)
        page = ctx.new_page()
        try:
            if not ir_al_inicio(page):
                # Avisamos una sola vez hasta que se vuelva a loguear.
                if not estado.get("aviso_sesion_vencida"):
                    comando = plataforma().comando("login")
                    enviar_mail(
                        "SIU: hay que volver a iniciar sesión",
                        f"No pude entrar al SIU. Corré en la carpeta siu:\n\n    {comando}\n",
                        mail_ui.html_aviso(
                            "Sesión caída",
                            "No pude entrar al SIU. Corré esto en la carpeta siu para volver a loguearte:",
                            comando,
                            INICIO,
                        ),
                    )
                    estado["aviso_sesion_vencida"] = True
                    guardar_estado(estado)
                log("Sesión vencida.")
                sys.exit(2)
            estado.pop("aviso_sesion_vencida", None)
            lectura = leer_todo(page)
        finally:
            ctx.close()
    creditos = lectura["creditos"]

    # La foto acumula todo lo visto (no se reemplaza): si una corrida lee de menos
    # (un Detalle que no cargó, una materia que pasó de "en curso" a la historia),
    # esas filas no vuelven a avisarse como nuevas cuando reaparecen.
    novedades: dict[str, list[str]] = {}
    for clave, filas in lectura["notas"].items():
        previas = estado.get(clave)
        if previas is None:
            log(f"{clave}: primera corrida, guardo {len(filas)} filas como base.")
            estado[clave] = filas
            continue
        vistas = set(previas)
        nuevas = [f for f in filas if f not in vistas]
        if nuevas:
            novedades[clave] = nuevas
        log(f"{clave}: {len(filas)} filas, {len(nuevas)} nuevas.")
        estado[clave] = previas + nuevas

    # Créditos: avisamos cuando aparece una comisión nueva que da créditos (no por
    # cambios de cupo, que cambia todo el tiempo). La primera vez manda todas las actuales.
    vistas_cred = set(estado.get("creditos", []))
    nuevas_cred = [c for c in creditos if clave_credito(c) not in vistas_cred]
    log(f"creditos: {len(creditos)} comisiones con créditos, {len(nuevas_cred)} nuevas.")
    estado["creditos"] = estado.get("creditos", []) + [clave_credito(c) for c in nuevas_cred]

    # Inasistencias: a diferencia de las notas, pueden bajar o justificarse, así que la
    # foto se REEMPLAZA y se compara completa (nuevas, quitadas y modificadas).
    faltas = lectura["inasistencias"]
    previas_faltas = estado.get("inasistencias")
    cambios_faltas: list[dict] = []
    if previas_faltas is None:
        log(f"inasistencias: primera corrida, guardo {len(faltas)} como base.")
    else:
        cambios_faltas = diff_inasistencias(previas_faltas, faltas)
        log(f"inasistencias: {len(faltas)} registros, {sum(len(c['cambios']) for c in cambios_faltas)} cambios.")
    estado["inasistencias"] = faltas

    # Exámenes: mesas nuevas de las materias en curso. Igual que créditos, se acumula
    # lo visto y la primera vez se mandan todas las que haya.
    mesas = lectura["examenes"]
    vistas_mesas = set(estado.get("examenes", []))
    nuevas_mesas = [m for m in mesas if clave_examen(m) not in vistas_mesas]
    log(f"examenes: {len(mesas)} mesas de materias en curso, {len(nuevas_mesas)} nuevas.")
    estado["examenes"] = estado.get("examenes", []) + [clave_examen(m) for m in nuevas_mesas]

    if novedades or nuevas_cred or cambios_faltas or nuevas_mesas:
        enviar_novedades(novedades, nuevas_cred, cambios_faltas, examenes=nuevas_mesas)
    estado["ultima_corrida"] = datetime.now().isoformat(timespec="seconds")
    guardar_estado(estado)


def enviar_novedades(
    novedades: dict[str, list[str]],
    creditos: list[dict] | None = None,
    faltas: list[dict] | None = None,
    prefijo: str = "",
    examenes: list[dict] | None = None,
) -> None:
    creditos, faltas, examenes = creditos or [], faltas or [], examenes or []
    total = sum(len(v) for v in novedades.values())
    resumen = []
    if total:
        resumen.append(f"{total} nota{'s' if total != 1 else ''} nueva{'s' if total != 1 else ''}")
    if creditos:
        resumen.append(f"{len(creditos)} actividad{'es' if len(creditos) != 1 else ''} con créditos")
    if faltas:
        resumen.append(f"inasistencias en {len(faltas)} materia{'s' if len(faltas) != 1 else ''}")
    if examenes:
        resumen.append(f"{len(examenes)} mesa{'s' if len(examenes) != 1 else ''} de examen")
    bloques = [f"== {TITULOS[k]} ==\n" + "\n".join(f"• {f}" for f in filas) for k, filas in novedades.items()]
    if examenes:
        bloques.append(
            "== Mesas de examen (materias en curso) ==\n" + "\n".join(f"• {texto_examen(m)}" for m in examenes)
            + "\n\nAdjunto: examenes.ics para agregarlas a tu calendario."
        )
    if creditos:
        bloques.append("== Actividades con créditos ==\n" + "\n".join(f"• {texto_credito(c)}" for c in creditos))
    if faltas:
        bloques.append("== Inasistencias ==\n" + "\n".join(f"• {texto_inasistencias(c)}" for c in faltas))
    enviar_mail(
        f"{prefijo}SIU ▸ " + " · ".join(resumen),
        "\n\n".join(bloques) + f"\n\n{INICIO}",
        mail_ui.html_novedades(novedades, TITULOS, INICIO, creditos, f"{SIU}/cursada", faltas, examenes),
        [("examenes.ics", ics_examenes(examenes), "calendar")] if examenes else None,
    )


def cmd_test_mail() -> None:
    """Manda un mail de muestra con algunas notas reales ya vistas (o una de ejemplo)."""
    estado = cargar_estado()
    muestra = {k: estado[k][-3:] for k in TITULOS if estado.get(k)}
    if not muestra:
        muestra = {"en_curso": ["Materia de prueba (TEST): Parcial - Primer Parcial - 9.20 (Nueve con 20/100) Aprobado 15/09/2026"]}
    enviar_novedades(muestra, prefijo="[PRUEBA] ")  # sin créditos: requieren scrapear cursada


def en_silencio(ahora: datetime | None = None) -> bool:
    """SIU_SILENCIO="0-7" -> no revisa de 00:00 a 06:59. Admite cruzar medianoche ("23-7")."""
    rango = os.getenv("SIU_SILENCIO", "").strip()
    if not rango:
        return False
    try:
        desde, hasta = (int(x) for x in rango.split("-"))
    except ValueError:
        log(f"SIU_SILENCIO inválido ({rango!r}), uso formato 'desde-hasta' en horas, ej. 0-7.")
        return False
    hora = (ahora or datetime.now()).hour
    return desde <= hora < hasta if desde <= hasta else hora >= desde or hora < hasta


REINTENTO_SEG = 60       # espera antes del segundo intento dentro de la misma corrida
FALLOS_PARA_AVISAR = 3   # corridas seguidas con error antes de mandar el mail


def check_con_aviso_de_error() -> None:
    """`check` para la tarea programada. Los errores pasajeros (sin red al despertar
    la notebook, el SIU lento) no avisan: reintenta una vez dentro de la corrida y
    solo manda mail si fallan FALLOS_PARA_AVISAR corridas seguidas. Ese mail sale
    UNA vez hasta que vuelva a andar. Las corridas sin cambios no mandan nada."""
    if en_silencio():
        log(f"Horario de silencio (SIU_SILENCIO={os.getenv('SIU_SILENCIO')}), no reviso.")
        return
    try:
        try:
            cmd_check()
        except SystemExit:
            raise
        except Exception as e:
            log(f"Falló el check ({type(e).__name__}: {str(e).splitlines()[0]}); reintento en {REINTENTO_SEG} s.")
            time.sleep(REINTENTO_SEG)
            cmd_check()
    except SystemExit:
        raise
    except Exception:
        log("Error en check:\n" + traceback.format_exc())
        estado = cargar_estado()
        estado["fallos_seguidos"] = estado.get("fallos_seguidos", 0) + 1
        guardar_estado(estado)
        if estado["fallos_seguidos"] < FALLOS_PARA_AVISAR:
            log(f"Fallo {estado['fallos_seguidos']}/{FALLOS_PARA_AVISAR} seguido; todavía no aviso.")
            sys.exit(1)
        if not estado.get("aviso_error"):
            try:
                enviar_mail(
                    "SIU: el monitor de notas está fallando",
                    f"Revisá el log en {LOG}\n\n{traceback.format_exc()}",
                    mail_ui.html_aviso(
                        "Monitor con errores",
                        f"Las últimas {FALLOS_PARA_AVISAR} revisiones del SIU fallaron. "
                        "Mirá el log o corré el check a mano para ver el error:",
                        plataforma().comando("check"),
                        INICIO,
                    ),
                )
            except Exception:
                log("Tampoco pude mandar el mail de error:\n" + traceback.format_exc())
                sys.exit(1)
            estado["aviso_error"] = True
            guardar_estado(estado)
        sys.exit(1)
    else:
        estado = cargar_estado()
        avisado = estado.pop("aviso_error", None)
        fallos = estado.pop("fallos_seguidos", None)
        if avisado or fallos:
            guardar_estado(estado)
            log("Volvió a funcionar.")


def cmd_verificar() -> None:
    problemas = plataforma().verificar_requisitos()
    for p in problemas:
        log(f"✗ {p}")
    if problemas:
        sys.exit(1)
    log(f"✓ Requisitos OK en {plataforma().nombre}.")


def cmd_programar() -> None:
    intervalo = int(os.getenv("SIU_INTERVALO_MIN", "60"))
    cmd_verificar()
    log(f"Programando '{NOMBRE_TAREA}' cada {intervalo} min en {plataforma().nombre}.")
    log(plataforma().programar(NOMBRE_TAREA, intervalo))


def cmd_desprogramar() -> None:
    plataforma().desprogramar(NOMBRE_TAREA)
    log(f"Tarea '{NOMBRE_TAREA}' eliminada.")


if __name__ == "__main__":
    comandos = {
        "login": cmd_login,
        "check": check_con_aviso_de_error,
        "dump": cmd_dump,
        "test-mail": cmd_test_mail,
        "verificar": cmd_verificar,
        "programar": cmd_programar,
        "desprogramar": cmd_desprogramar,
    }
    if len(sys.argv) != 2 or sys.argv[1] not in comandos:
        sys.exit(f"Uso: python siu_notas.py [{'|'.join(comandos)}]")
    comandos[sys.argv[1]]()
