"""Avisa por mail cuando aparecen notas nuevas en el SIU Guaraní de la Austral.

El login es por Microsoft (SAML). Con Edge y la cuenta de la Austral vinculada a
Windows, el SSO entra solo; si no, se hace una vez a mano y queda en ./perfil.
Lo que depende del sistema operativo (navegador, venv, tarea programada) vive en
el paquete `plataformas`.
  - `python siu_notas.py check`        -> headless: elige la carrera y lee parciales,
     Actuación provisoria, Historia académica y actividades con créditos; compara
     contra lo ya visto y manda un mail si hay novedades.
  - `python siu_notas.py inscribir ALIAS... [--profesor X] [--simular]` -> polling cada 5-10 s
     en Inscripción a materias y se anota en la primera comisión con cupo. Es el único
     comando que ESCRIBE en el SIU; `check` sigue siendo solo lectura.
  - `python siu_notas.py --usuario X <comando>` -> varios usuarios en la misma PC: sesión, estado y
     log propios en usuarios/X/. `inscribir ... --usuarios a,b,c` los lanza a todos en paralelo.
  - `python siu_notas.py login`        -> abre el navegador para loguearte a mano (MFA).
  - `python siu_notas.py dump`         -> guarda lo leído en ./capturas/filas.json.
  - `python siu_notas.py test-mail`    -> prueba la config SMTP.
  - `python siu_notas.py test-whatsapp` -> prueba el aviso por WhatsApp (CallMeBot).
  - `python siu_notas.py verificar`    -> chequea los requisitos del sistema.
  - `python siu_notas.py programar`    -> crea la ejecución periódica (SIU_INTERVALO_MIN).
  - `python siu_notas.py desprogramar` -> la elimina.
"""

import argparse
import base64
import hashlib
import json
import os
import random
import re
import signal
import smtplib
import subprocess
import sys
import time
import traceback
import unicodedata
import zlib
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values, load_dotenv
from playwright.sync_api import BrowserContext, Page, sync_playwright

import mail_ui as mail_ui_futurista
import mail_ui_siu
import plataformas
import whatsapp

BASE = Path(__file__).parent


def _usuario_de_argv() -> str:
    """Varios usuarios en la misma PC: `siu_notas.py --usuario NOMBRE <comando>` (o la variable
    SIU_USUARIO). Cada usuario tiene su propia sesión del SIU, estado, log y .env en usuarios/NOMBRE/.
    Sin usuario, todo queda como siempre (perfil/, estado/, .env)."""
    usuario = os.getenv("SIU_USUARIO", "").strip()
    if __name__ == "__main__" and len(sys.argv) >= 2 and sys.argv[1] == "--usuario":
        if len(sys.argv) < 3:
            sys.exit("Falta el nombre: siu_notas.py --usuario NOMBRE <comando>")
        usuario = sys.argv[2].strip()
        del sys.argv[1:3]  # el resto del programa ve solo el comando
    if usuario and not re.fullmatch(r"[a-z0-9_-]+", usuario):
        sys.exit(f"Usuario inválido ({usuario!r}): solo minúsculas, números, '-' y '_'.")
    return usuario


USUARIO = _usuario_de_argv()
RAIZ = BASE / "usuarios" / USUARIO if USUARIO else BASE
PERFIL = RAIZ / "perfil"
ESTADO = RAIZ / "estado" / "snapshot.json"
CAPTURAS = RAIZ / "capturas"
# Existe mientras `inscribir` corre (se "toca" en cada vuelta): la tarea programada
# no puede abrir el mismo perfil al mismo tiempo, así que se salta esa revisión.
LOCK_INSCRIPCION = RAIZ / "estado" / "inscribiendo.lock"
LOCK_VIVO_SEG = 180

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
NOMBRE_TAREA_INSCRIPCION = "SIU inscripcion"  # arranque automático de `inscribir` al iniciar sesión

LOG = RAIZ / "estado" / "siu.log"
LOG_MAX = 1_000_000  # bytes; al pasarlo se queda con la segunda mitad

# El .env del usuario va primero y PISA lo heredado (override=True: un `inscribir --usuarios` lanza
# hijos que ya traen el .env principal en su entorno); lo que no defina lo hereda del .env principal.
if USUARIO:
    load_dotenv(RAIZ / ".env", override=True)
load_dotenv(BASE / ".env")
# Las cookies se leen siempre del archivo de CADA usuario (ver más abajo), nunca del entorno: así no se
# heredan del .env principal, y el bloque de entorno de los procesos hijos no crece (en Windows tiene tope).
os.environ.pop("SIU_COOKIES", None)
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
    # Con usuario, la línea lo nombra: varias consolas/procesos comparten pantalla.
    linea = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] " + (f"[{USUARIO}] " if USUARIO else "") + msg
    if sys.stdout:
        print(linea, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
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


def comando_cli(*args: str) -> str:
    """Comando para mostrar al usuario (mails, mensajes); con usuario, incluye `--usuario X`."""
    return plataforma().comando(*(("--usuario", USUARIO) if USUARIO else ()), *args)


# ---------- cookies en el .env ----------
# Chromium guarda la sesión en perfil/ cifrada con tu usuario de Windows: copiar esa carpeta a otra PC no
# sirve. Por eso la sesión también se puede guardar como texto en el .env del usuario (SIU_COOKIES), y
# alcanza con migrar ese archivo. Son credenciales en texto plano: tratalas como una contraseña.

CLAVE_COOKIES = "SIU_COOKIES"
NOMBRE_USUARIO = re.compile(r"[a-z0-9_-]+")


def env_de(usuario: str) -> Path:
    """.env de un usuario (el principal si `usuario` está vacío)."""
    return BASE / "usuarios" / usuario / ".env" if usuario else BASE / ".env"


ENV_USUARIO = env_de(USUARIO)


def codificar_cookies(cookies: list[dict]) -> str:
    return base64.b64encode(zlib.compress(json.dumps(cookies, separators=(",", ":")).encode("utf-8"), 9)).decode("ascii")


def leer_cookies_env(env: Path) -> list[dict]:
    """Cookies guardadas en ese .env (SIU_COOKIES). Se lee del ARCHIVO y no del entorno: un usuario sin
    cookies propias jamás debe entrar con las de otro."""
    crudo = (dotenv_values(env).get(CLAVE_COOKIES) or "").strip() if env.exists() else ""
    if not crudo:
        return []
    try:
        cookies = json.loads(zlib.decompress(base64.b64decode(crudo)).decode("utf-8"))
        return cookies if isinstance(cookies, list) else []
    except Exception:
        log(f"{CLAVE_COOKIES} en {env.name} está dañado; lo ignoro (corré `login` o `cookies` de nuevo).")
        return []


def escribir_en_env(env: Path, clave: str, valor: str) -> None:
    """Pone `clave=valor` en el .env: reemplaza la línea si existe, si no la agrega. No toca el resto."""
    env.parent.mkdir(parents=True, exist_ok=True)
    lineas = env.read_text(encoding="utf-8").splitlines() if env.exists() else []
    nueva = f"{clave}={valor}"
    for i, linea in enumerate(lineas):
        if re.match(rf"\s*{re.escape(clave)}\s*=", linea):
            lineas[i] = nueva
            break
    else:
        lineas.append(nueva)
    env.write_text("\n".join(lineas) + "\n", encoding="utf-8")


def guardar_cookies(ctx: BrowserContext) -> int:
    """Guarda las cookies de la sesión abierta en el .env de este usuario. Devuelve cuántas."""
    cookies = ctx.cookies()
    escribir_en_env(ENV_USUARIO, CLAVE_COOKIES, codificar_cookies(cookies))
    return len(cookies)


_cookies_sembradas = False


def sembrar_cookies(ctx: BrowserContext) -> bool:
    """Carga en el navegador las cookies guardadas en el .env (una vez por proceso). True si cargó alguna."""
    global _cookies_sembradas
    cookies = [] if _cookies_sembradas else leer_cookies_env(ENV_USUARIO)
    _cookies_sembradas = True
    # Solo las que el perfil no tiene: las suyas pueden ser más frescas (Microsoft las va renovando) y las del
    # .env son de la última exportación. La cookie de sesión del SIU no se persiste al cerrar Chromium: esa sí falta.
    tiene = {(c["name"], c["domain"], c["path"]) for c in ctx.cookies()}
    cookies = [c for c in cookies if (c.get("name"), c.get("domain"), c.get("path")) not in tiene]
    if not cookies:
        return False
    try:
        ctx.add_cookies(cookies)
    except Exception as e:
        log(f"No pude cargar las cookies del .env ({type(e).__name__}: {str(e).splitlines()[0]}).")
        return False
    log(f"Cargué {len(cookies)} cookies guardadas en {ENV_USUARIO.relative_to(BASE).as_posix()}.")
    return True


def tiene_sesion(usuario: str) -> bool:
    """¿Ese usuario tiene con qué entrar al SIU? (perfil de Chromium o cookies en su .env)"""
    return (BASE / "usuarios" / usuario / "perfil").exists() or bool(leer_cookies_env(env_de(usuario)))


def usuarios_existentes() -> list[str]:
    carpeta = BASE / "usuarios"
    if not carpeta.exists():
        return []
    return sorted(d.name for d in carpeta.iterdir()
                  if d.is_dir() and NOMBRE_USUARIO.fullmatch(d.name) and tiene_sesion(d.name))


# ---------- navegador ----------

def abrir_contexto(p, headless: bool, sembrar: bool = True) -> BrowserContext:
    PERFIL.mkdir(parents=True, exist_ok=True)
    canal = os.getenv("SIU_NAVEGADOR") or plataforma().canal_navegador
    if canal.lower() == "chromium":
        canal = None  # el Chromium que descarga Playwright (`playwright install chromium`), no uno del sistema
    ctx = p.chromium.launch_persistent_context(
        str(PERFIL), channel=canal, headless=headless, locale="es-AR", viewport={"width": 1280, "height": 900}
    )
    # Perfil sin sesión del SIU (nuevo, o migrado a otra PC con solo el .env): arranca con las cookies guardadas.
    if sembrar and not any(c["domain"].lstrip(".").endswith("siu.austral.edu.ar") for c in ctx.cookies()):
        sembrar_cookies(ctx)
    return ctx


def en_siu(url: str) -> bool:
    return urlparse(url).hostname == "siu.austral.edu.ar"


def esta_logueado_url(url: str) -> bool:
    return en_siu(url) and "/acceso" not in urlparse(url).path


def esta_logueado(page: Page) -> bool:
    return esta_logueado_url(page.url)


def ir_al_inicio(page: Page) -> bool:
    """Entra al inicio de alumno. Si no hay sesión, prueba con las cookies guardadas en el .env (una vez)."""
    if _entrar(page):
        return True
    return sembrar_cookies(page.context) and _entrar(page)


def _entrar(page: Page) -> bool:
    """Si la sesión del SIU venció, dispara el SSO de Microsoft, que vuelve solo si la sesión de Microsoft
    sigue viva."""
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


def leer_historia(page: Page) -> tuple[list[str], dict[str, dict]]:
    """Notas de la historia completa y finales pendientes.
    - filas: una por nota, 'Materia (COD): Examen - 7.00 (Siete) Aprobado 18/07/2024'.
    - pendientes: {código: {materia, vencimiento}} de materias con la regularidad aprobada
      y sin examen aprobado, promoción ni equivalencia (= falta rendir el final)."""
    page.goto(HISTORIA, wait_until="networkidle")
    page.click("a[consulta='todo']")
    page.wait_for_selector("#listado .catedras", timeout=30_000)
    page.wait_for_load_state("networkidle")
    bloques = page.eval_on_selector_all(
        "#listado .catedras",
        r"""bloques => bloques.map(b => ({
            materia: b.querySelector('h3')?.innerText.trim() ?? '',
            // Cada .catedra trae su estado como atributo: examen="Aprobado", regularidad="Aprobado",
            // promocion="Promocionado", equivalencia="Aprobado", encurso=..., examen="Reprobado".
            catedras: [...b.querySelectorAll('.catedra')].map(c => ({
                texto: c.querySelector('.catedra_nombre')?.innerText ?? '',
                estados: [...c.attributes].filter(a => a.name !== 'class').map(a => [a.name, a.value]),
                vencimiento: (c.querySelector('.toggle_info_catedra')?.textContent.match(/Vencimiento:\s*(\d{2}\/\d{2}\/\d{4})/) || [])[1] ?? '',
            })),
        }))""",
    )
    filas, pendientes = [], {}
    for b in bloques:
        # "En curso - Inicio de dictado ..." no es una nota.
        filas += [f"{b['materia']}: {limpiar(c['texto'])}" for c in b["catedras"] if c["texto"]
                  and not limpiar(c["texto"]).startswith("En curso")]
        estados = [(n, v) for c in b["catedras"] for n, v in c["estados"]]
        regular = any(n == "regularidad" and v == "Aprobado" for n, v in estados)
        aprobada = any(n not in ("regularidad", "encurso") and v in ("Aprobado", "Promocionado") for n, v in estados)
        en_curso = any(n == "encurso" for n, _ in estados)
        m = re.search(r"\(([^()]+)\)\s*$", b["materia"])
        if regular and not aprobada and not en_curso and m:
            vencimientos = [c["vencimiento"] for c in b["catedras"]
                            if ("regularidad", "Aprobado") in [tuple(e) for e in c["estados"]] and c["vencimiento"]]
            # La regularidad más reciente es la que vale.
            vencimiento = max(vencimientos, key=lambda d: _fecha_hora(d) or datetime.min, default="")
            pendientes[m.group(1)] = {"materia": b["materia"], "vencimiento": vencimiento}
    return list(dict.fromkeys(filas)), pendientes


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
    # "30 | Inscriptos: 12" o, sin tope, "Sin definir | Inscriptos: 26" (total = None).
    cupo = re.search(r"([^|]*?)\s*\|\s*Inscriptos:\s*(\d+)", cupo_txt)
    creditos = re.search(r"(\d+(?:[.,]\d+)?)\s*cr[eé]ditos?", obs, re.I)
    asistencia = re.search(r"asistencia[^\d%]*(\d{1,3})\s*%", obs, re.I)
    lineas = [" ".join(l.split()) for l in obs.splitlines() if l.strip()]
    cuando = [l for l in lineas if re.search(DIAS, l, re.I) or re.search(r"\b\d{1,2}/\d{1,2}\b", l)]
    total = int(cupo.group(1)) if cupo and cupo.group(1).strip().isdigit() else None
    inscriptos = int(cupo.group(2)) if cupo else None
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


def links_cursada(page: Page) -> list[tuple[str, str]]:
    """[(título, href)] de las materias de Inscripción a materias. Va a /cursada (GET)."""
    page.goto(f"{SIU}/cursada", wait_until="networkidle")
    return list(dict(page.eval_on_selector_all(
        "a[href*='/cursada/elegir_materia/']", "as => as.map(a => [a.title, a.href])"
    )).items())


def nombre_materia(titulo: str) -> str:
    return re.sub(r"\s*-\s*\(", " (", titulo)  # "Ayudantía 6 - (COD)" -> "Ayudantía 6 (COD)"


def leer_comisiones(page: Page, titulo: str) -> list[dict]:
    """Comisiones de la materia que ya está abierta en `page` (cursada/elegir_materia/<id>).
    Cada una trae `indice` (posición entre las `li.comision`) y `docentes`. Solo lee."""
    materia = nombre_materia(titulo)
    crudas = page.eval_on_selector_all(
        "#comisiones li.comision",
        """lis => lis.map((li, i) => {
            const campo = (nombres, crudo = false, ausente = '') => {
                for (const row of li.querySelectorAll('.row-fluid')) {
                    const k = row.querySelector('.span3');
                    const t = k ? k.innerText.trim().toLowerCase() : '';
                    if (nombres.some(n => t.startsWith(n)))
                        { const v = row.querySelector('.span9'); return (crudo ? v?.textContent : v?.innerText) ?? ''; }
                }
                return ausente;
            };
            return [
                (li.querySelector('h4')?.innerText ?? '').replace(/^\\s*Comisi[oó]n:\\s*/, '').trim(),
                campo(['cupo']), campo(['observaciones'], true),
                !li.classList.contains('no-inscripto'),
                i,
                // Fila "Docentes:" (puede estar vacía). Solo si la página no la tiene, el texto entero de la comisión.
                campo(['docente', 'profesor'], false, null) ?? li.innerText,
            ];
        })""",
    )
    comisiones = []
    for comision, cupo, obs, inscripto, indice, docentes in crudas:
        info = _info_comision(materia, comision, cupo, obs, inscripto)
        comisiones.append({**info, "indice": indice, "docentes": " ".join(docentes.split())})
    return comisiones


def leer_creditos(page: Page) -> list[dict]:
    """Comisiones de Inscripción a materias que dan créditos (según 'Observaciones').
    Solo lee: entra a cursada/elegir_materia/<id> (GET) y NUNCA envía el form de inscripción."""
    comisiones = []
    for titulo, href in links_cursada(page):
        page.goto(href, wait_until="networkidle")
        comisiones += [c for c in leer_comisiones(page, titulo) if c["creditos"]]
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


def leer_examenes(page: Page, motivos: dict[str, str]) -> list[dict]:
    """Mesas de Inscripción a exámenes de las materias que te interesan, cruzadas por código.
    `motivos` = {código: por qué se vigila} (ej. "Cursando", "Final pendiente · regular hasta ...").
    Solo lee: entra a examen/elegir_materia/<id> (GET) y NUNCA envía el form de inscripción."""
    page.goto(f"{SIU}/examen", wait_until="networkidle")
    links = page.eval_on_selector_all(
        "a[href*='/examen/elegir_materia/']", "as => as.map(a => [a.title, a.href])"
    )
    mesas = []
    for titulo, href in dict(links).items():
        m = re.search(r"\(([^()]+)\)\s*$", titulo)
        if not m or m.group(1) not in motivos:
            continue
        motivo = motivos[m.group(1)]
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
                "motivo": motivo,
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
    motivo = f" ({m['motivo']})" if m.get("motivo") else ""
    return f"{m['fecha']} — {m['materia']}{motivo}{estado}: " + " | ".join(p for p in partes if p)


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
                ("Motivo", m.get("motivo", "")), ("Turno", m["turno"]), ("Llamado", m["llamado"]), ("Mesa", m["mesa"]),
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


def motivos_examen(en_curso: set[str], pendientes: dict[str, dict], hoy: datetime | None = None) -> dict[str, str]:
    """{código: motivo} de las materias cuyas mesas interesan: las que cursás y los finales pendientes."""
    hoy = hoy or datetime.now()
    motivos = {}
    for codigo, p in pendientes.items():
        venc = _fecha_hora(p["vencimiento"]) if p["vencimiento"] else None
        if not venc:
            motivos[codigo] = "Final pendiente"
        elif venc.date() >= hoy.date():
            motivos[codigo] = f"Final pendiente · regular hasta {p['vencimiento']}"
        else:
            motivos[codigo] = f"Final pendiente · regularidad vencida el {p['vencimiento']} (¿libre?)"
    motivos.update({c: "Cursando" for c in en_curso})  # si está en curso, ese es el motivo
    return motivos


def leer_todo(page: Page) -> dict:
    """{'notas': {sección: [filas]}, 'creditos': [comisiones], 'inasistencias': {clave: falta},
    'examenes': [mesas de materias en curso y finales pendientes], 'pendientes': {código: ...}}."""
    elegir_carrera(page)
    en_curso, codigos_en_curso = leer_en_curso(page)
    historia, pendientes = leer_historia(page)
    return {
        "notas": {
            "en_curso": en_curso,
            "actuacion": leer_actuacion(page),
            "historia": historia,
        },
        "creditos": leer_creditos(page),
        "inasistencias": leer_inasistencias(page),
        "examenes": leer_examenes(page, motivos_examen(codigos_en_curso, pendientes)),
        "pendientes": pendientes,
    }


# ---------- inscripción automática (único código que ESCRIBE en el SIU) ----------

class Ambiguo(RuntimeError):
    """Más de una materia matchea con la misma fuerza: no se adivina."""


def compacto(texto: str) -> str:
    """Solo letras y números en minúscula: 'Hack AI' y 'HackAI' quedan iguales."""
    return re.sub(r"[^a-z0-9]", "", normalizar(texto))


def coincide(titulo: str, alias: str) -> float:
    """Qué tan bien el alias describe el título de la materia (0 = no coincide, 1 = exacto).
    Acepta el código ('GIHACK'), el nombre en cualquier forma ('hack ai') y typos ('hackthon')."""
    a, t = compacto(alias), compacto(titulo)
    if not a:
        return 0.0
    codigo = re.search(r"\(([^()]+)\)\s*$", titulo)
    if (codigo and compacto(codigo.group(1)) == a) or a in t:
        return 1.0
    # Con alias cortos el difuso da falsos positivos: "hackai" ~ "hacki|ng" (Hacking Ético) llegaba a 0.91.
    # Solo se toleran typos en alias largos, y con ventanas de largo casi igual al alias.
    if len(a) < 7:
        return 0.0
    mejor = 0.0
    for largo in range(len(a) - 1, len(a) + 2):
        for i in range(len(t) - largo + 1):
            mejor = max(mejor, SequenceMatcher(None, a, t[i:i + largo]).ratio())
    return mejor if mejor >= 0.85 else 0.0


def docente_coincide(texto: str, profesor: str) -> bool:
    """Todas las palabras del nombre aparecen en el texto, en cualquier orden y sin tildes
    ('Salaberri, Martín' == 'martin salaberri')."""
    palabras = re.findall(r"[a-z0-9]+", normalizar(profesor))
    presentes = set(re.findall(r"[a-z0-9]+", normalizar(texto)))
    return bool(palabras) and all(p in presentes for p in palabras)


def buscar_materia(links: list[tuple[str, str]], aliases: list[str]) -> tuple[str, str] | None:
    """La materia de `links` [(título, href)] que mejor matchea con algún alias.
    Si varias empatan en el mejor puntaje, lanza Ambiguo en vez de elegir una."""
    puntajes = [(max(coincide(t, a) for a in aliases), t, h) for t, h in links]
    mejor = max((p for p, _, _ in puntajes), default=0.0)
    if mejor == 0.0:
        return None
    ganadoras = [(t, h) for p, t, h in puntajes if p == mejor]
    if len(ganadoras) > 1:
        raise Ambiguo("Varias materias matchean igual: " + "; ".join(t for t, _ in ganadoras)
                      + ". Usá un alias más específico (o el código entre paréntesis).")
    return ganadoras[0]


def elegir_comision(comisiones: list[dict]) -> dict | None:
    """La primera comisión con cupo (o con cupo desconocido) a la que todavía no estás inscripto."""
    return next((c for c in comisiones if not c["inscripto"] and c["quedan"] != 0), None)


def guardar_captura(page: Page, nombre: str) -> str:
    """Screenshot + HTML de la página actual en ./capturas, para depurar. Devuelve el prefijo."""
    CAPTURAS.mkdir(parents=True, exist_ok=True)
    base = CAPTURAS / f"{nombre}_{datetime.now():%Y%m%d_%H%M%S}"
    try:
        page.screenshot(path=str(base.with_suffix(".png")), full_page=True)
        base.with_suffix(".html").write_text(page.content(), encoding="utf-8")
    except Exception as e:
        log(f"No pude guardar la captura: {e}")
    return str(base)


def boton_inscripcion(page: Page, indice: int):
    """Locator del botón 'Inscribirse' dentro de la comisión nº `indice` (puede no existir).
    'Dar de baja' / 'Desinscribirse' nunca matchean."""
    li = page.locator("#comisiones li.comision").nth(indice)
    patron = re.compile(r"^(?!.*(baja|desinscri)).*inscrib", re.I)
    return li.get_by_role("button", name=patron).or_(li.get_by_role("link", name=patron))


def inscribir_en_comision(page: Page, titulo: str, href: str, comision: dict) -> tuple[bool, str]:
    """Aprieta 'Inscribirse' en la comisión elegida (la página de la materia ya está abierta).
    Devuelve (ok, mensaje). Solo cuenta como éxito si, al recargar, el SIU la muestra como
    inscripta: el click por sí solo no prueba nada."""
    boton = boton_inscripcion(page, comision["indice"])
    if boton.count() == 0:
        return False, f"No encontré el botón de inscripción (capturas: {guardar_captura(page, 'inscripcion_sin_boton')})"
    boton.first.click()
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1000)
    # Algunos formularios piden confirmar en un modal.
    confirmar = page.locator(".modal:visible, .bootbox:visible").get_by_role(
        "button", name=re.compile(r"^\s*(aceptar|confirmar|inscribirse|inscribir|s[ií])\s*$", re.I))
    if confirmar.count():
        confirmar.first.click()
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(1000)
    mensajes = [" ".join(t.split()) for t in page.locator(".alert").all_inner_texts() if t.strip()]
    mensaje_siu = " | ".join(dict.fromkeys(mensajes))
    page.goto(href, wait_until="networkidle")
    # Ya inscripta, el SIU cambia el título de la comisión por "Estás inscripto en ..." (queda sin
    # nombre), así que no se puede comparar por nombre. Antes del click no había ninguna inscripta
    # (cmd_inscribir lo verifica), por lo que alguna inscripta ahora significa que se anotó.
    if any(c["inscripto"] for c in leer_comisiones(page, titulo)):
        return True, mensaje_siu
    donde = guardar_captura(page, "inscripcion_fallida")
    return False, f"{mensaje_siu or 'el SIU no confirmó la inscripción'} (capturas: {donde})"


# ---------- estado y mail ----------

def cargar_estado() -> dict:
    if ESTADO.exists():
        return json.loads(ESTADO.read_text(encoding="utf-8"))
    return {}


def guardar_estado(estado: dict) -> None:
    ESTADO.parent.mkdir(parents=True, exist_ok=True)
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

PLANTILLA_ENV_USUARIO = """\
# Config de este usuario. Lo que no definas acá lo hereda del .env principal (SMTP, navegador, etc.).
# Descomentá y completá solo lo que sea distinto:
# MAIL_TO=
# SIU_CARRERA=
# WHATSAPP_TELEFONO=
# WHATSAPP_APIKEY=
# SIU_COOKIES (la sesión de ESTE usuario) lo escribe `login` / `cookies`. No se hereda: es de esta persona.
"""


def cmd_login() -> None:
    if USUARIO:
        RAIZ.mkdir(parents=True, exist_ok=True)
        if not ENV_USUARIO.exists():
            ENV_USUARIO.write_text(PLANTILLA_ENV_USUARIO, encoding="utf-8")
            log(f"Creé {ENV_USUARIO.relative_to(BASE)} (opcional: MAIL_TO / SIU_CARRERA propios).")
    with sync_playwright() as p:
        ctx = abrir_contexto(p, headless=False, sembrar=False)  # login nuevo: no mezclar con cookies viejas
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(f"{SIU}/acceso?auth=saml")
        print("\nLogueate en la ventana (MFA incluido) y elegí 'Mantener la sesión iniciada: Sí'.")
        print("Espero hasta 5 minutos a que llegues al inicio del SIU...\n")
        page.wait_for_url(esta_logueado_url, timeout=300_000)
        page.wait_for_load_state("networkidle")
        if esta_logueado(page):
            n = guardar_cookies(ctx)
            log(f"Sesión guardada en ./{PERFIL.relative_to(BASE).as_posix()} y {n} cookies en "
                f"{ENV_USUARIO.relative_to(BASE).as_posix()} (para migrar solo con el .env). "
                "Ya podés correr `check` o `inscribir`.")
        else:
            log(f"No parece logueado (url: {page.url}).")
        ctx.close()


def cmd_cookies() -> None:
    """Exporta la sesión actual al .env de este usuario (SIU_COOKIES). Sirve para refrescarlas antes de migrar."""
    if inscripcion_en_curso():
        sys.exit("Hay un `inscribir` usando este perfil: esperá a que termine.")
    with sync_playwright() as p:
        ctx = abrir_contexto(p, headless=True)
        try:
            if not ir_al_inicio(ctx.new_page()):
                sys.exit(f"Sesión vencida: corré `{comando_cli('login')}` (el login ya guarda las cookies).")
            n = guardar_cookies(ctx)
        finally:
            ctx.close()
    log(f"{n} cookies guardadas en {ENV_USUARIO.relative_to(BASE).as_posix()} ({CLAVE_COOKIES}). "
        "Son credenciales en texto plano: no pegues ni compartas ese archivo.")


def cmd_usuarios() -> None:
    """Lista los usuarios (carpetas en usuarios/) y qué tiene cada uno para entrar."""
    nombres = usuarios_existentes()
    if not nombres:
        print("No hay usuarios. Agregá uno con: " + comando_cli("agregar-usuario", "NOMBRE"))
        return
    print(f"{'usuario':<16}{'perfil':<9}{'cookies en .env':<17}MAIL_TO propio")
    for u in nombres:
        env = env_de(u)
        propio = (dotenv_values(env).get("MAIL_TO") or "-") if env.exists() else "-"
        print(f"{u:<16}{'sí' if (BASE / 'usuarios' / u / 'perfil').exists() else '-':<9}"
              f"{'sí' if leer_cookies_env(env) else '-':<17}{propio}")
    print(f"\nTodos a la vez: {comando_cli('inscribir', 'ALIAS...', '--usuarios', 'todos')}")


def cmd_agregar_usuario(argv: list[str]) -> None:
    """`agregar-usuario NOMBRE`: abre el login de esa persona y deja su sesión lista (perfil + cookies en su .env)."""
    if USUARIO:
        sys.exit("agregar-usuario se usa sin --usuario.")
    if len(argv) != 1 or not NOMBRE_USUARIO.fullmatch(argv[0]):
        sys.exit("Uso: siu_notas.py agregar-usuario NOMBRE  (solo minúsculas, números, '-' y '_')")
    nombre = argv[0]
    if tiene_sesion(nombre):
        log(f"'{nombre}' ya tiene sesión: la renuevo.")
    sys.exit(subprocess.run([sys.executable, str(Path(__file__).resolve()), "--usuario", nombre, "login"]).returncode)


def cmd_dump() -> None:
    CAPTURAS.mkdir(parents=True, exist_ok=True)
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


def _pid_vivo(pid: int) -> bool:
    if sys.platform == "win32":  # os.kill(pid, 0) en Windows NO es un chequeo: manda Ctrl+C
        import ctypes
        kernel32 = ctypes.windll.kernel32
        proceso = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not proceso:
            return False
        try:
            codigo = ctypes.c_ulong()
            return bool(kernel32.GetExitCodeProcess(proceso, ctypes.byref(codigo))) and codigo.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(proceso)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def lock_vivo(lock: Path) -> bool:
    """¿Ese lock es de un `inscribir` vivo? Se toca en cada vuelta Y guarda el PID: tras un apagado brusco el
    lock puede ser reciente pero de un proceso que ya no existe (si no, el arranque automático lo respetaría)."""
    try:
        if time.time() - lock.stat().st_mtime >= LOCK_VIVO_SEG:
            return False
        return _pid_vivo(int(lock.read_text().strip()))
    except FileNotFoundError:
        return False
    except ValueError:  # lock sin PID (formato viejo): vale solo la fecha
        return True


def inscripcion_en_curso() -> bool:
    """¿Hay un `inscribir` vivo de este usuario? (comparte perfil con `check`)"""
    return lock_vivo(LOCK_INSCRIPCION)


# "Ya cumplida": si el pedido (alias + profesor) ya terminó con éxito, no vuelve a vigilar. Sin esto, el arranque
# automático seguiría buscando en cada encendido y, vía --profesor, podría anotarte en OTRA materia del mismo docente.
CUMPLIDAS = RAIZ / "estado" / "inscripciones.json"


def clave_pedido(aliases: list[str], profesor: str) -> str:
    base = "|".join(sorted(compacto(a) for a in aliases)) + "||" + normalizar(profesor)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def pedido_cumplido(clave: str) -> dict | None:
    try:
        return json.loads(CUMPLIDAS.read_text(encoding="utf-8")).get(clave)
    except (FileNotFoundError, ValueError):
        return None


def marcar_cumplido(clave: str, materia: str) -> None:
    try:
        datos = json.loads(CUMPLIDAS.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        datos = {}
    datos[clave] = {"materia": materia, "fecha": datetime.now().isoformat(timespec="seconds")}
    CUMPLIDAS.parent.mkdir(parents=True, exist_ok=True)
    CUMPLIDAS.write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")


class SesionCaida(RuntimeError):
    pass


REFRESCO_DOCENTES_SEG = 600   # cada cuánto se vuelven a leer los docentes de todas las materias
LATIDO_SEG = 300              # cada cuánto repetir en el log "sigo esperando" si nada cambió
ESPERA_ERROR_SEG = 30
ERRORES_PARA_RENDIRSE = 20
INTENTOS_FALLIDOS_PARA_RENDIRSE = 3


def localizar_materia(page: Page, aliases: list[str], profesor: str, cache: dict) -> tuple[tuple[str, str] | None, str]:
    """(materia, motivo_de_espera). Primero por alias; si no aparece y hay `profesor`, por docente
    (último recurso). `cache` guarda los docentes ya leídos para no abrir todas las materias en cada vuelta."""
    links = links_cursada(page)
    if not esta_logueado(page):
        raise SesionCaida()
    if not links:
        return None, "Inscripción a materias no tiene ninguna materia abierta"
    encontrada = buscar_materia(links, aliases)
    if encontrada:
        return encontrada, ""
    if not profesor:
        return None, f"ninguna de las {len(links)} materias abiertas matchea con {aliases}"
    if time.time() - cache.get("ts", 0) > REFRESCO_DOCENTES_SEG:
        cache.update(ts=time.time(), docentes={})
    docentes = cache.setdefault("docentes", {})
    for titulo, href in links:
        if href not in docentes:
            page.goto(href, wait_until="networkidle")
            comisiones = leer_comisiones(page, titulo)
            docentes[href] = (" ".join(c["docentes"] for c in comisiones), any(c["inscripto"] for c in comisiones))
    # Las materias donde ya estás inscripto no pueden ser el objetivo: sin esto, un profesor que dicta
    # otra materia tuya haría creer que ya estás anotado (o te anotaría en la que no es).
    candidatas = [(t, h) for t, h in links if docente_coincide(docentes[h][0], profesor) and not docentes[h][1]]
    if len(candidatas) == 1:
        if cache.get("via_profesor") != candidatas[0][1]:
            cache["via_profesor"] = candidatas[0][1]
            log(f"No matcheó ningún alias; la encontré por el profesor '{profesor}': {candidatas[0][0]}")
        return candidatas[0], ""
    if candidatas:
        return None, ("ambiguo: el profesor aparece en varias materias (" + "; ".join(t for t, _ in candidatas)
                      + "); no me anoto en ninguna sin que un alias la identifique")
    return None, f"ningún alias matchea y '{profesor}' no dicta ninguna de las {len(links)} materias abiertas"


def avisar_inscripcion(titulo: str, mensaje: str, detalle: str) -> None:
    """Mail + WhatsApp. El WhatsApp pasa por CallMeBot: sin WHATSAPP_DETALLE=completo no lleva el nombre de la materia.
    Con varios usuarios, el título dice de quién es el aviso."""
    if USUARIO:
        titulo = f"{titulo} ({USUARIO})"
    try:
        enviar_mail(f"SIU ▸ {titulo}", f"{mensaje}\n\n{detalle}\n\n{SIU}/cursada",
                    mail_ui.html_aviso(titulo, mensaje, detalle, f"{SIU}/cursada"))
    except Exception as e:
        log(f"No pude mandar el mail: {e}")
    avisar_whatsapp(f"*SIU ▸ {titulo}*\n" + (f"{mensaje}\n{detalle}" if whatsapp.detalle_completo()
                                              else "El detalle está en tu mail."))


def inscribir_varios(args: argparse.Namespace) -> None:
    """`inscribir --usuarios a,b,c`: un `inscribir` por usuario, en paralelo y en procesos aparte
    (cada uno con su sesión, polling, verificación y avisos). Comparten la consola; cada línea
    sale con el prefijo [usuario]."""
    if USUARIO:
        sys.exit("--usuarios se usa sin --usuario (lanza un proceso por cada usuario).")
    if args.usuarios.strip().lower() == "todos":  # cada usuario nuevo entra solo, sin tocar este comando
        usuarios = usuarios_existentes()
        if not usuarios:
            sys.exit("No hay usuarios con sesión. Agregá uno con: " + comando_cli("agregar-usuario", "NOMBRE"))
    else:
        usuarios = list(dict.fromkeys(u.strip() for u in args.usuarios.split(",") if u.strip()))
    invalidos = [u for u in usuarios if not NOMBRE_USUARIO.fullmatch(u)]
    if not usuarios or invalidos:
        sys.exit(f"--usuarios inválido ({invalidos or args.usuarios!r}): lista separada por comas (ej. juani,mateo) o 'todos'.")
    # Con perfil de Chromium o con cookies en su .env (migrado a otra PC) ya puede entrar.
    sin_sesion = [u for u in usuarios if not tiene_sesion(u)]
    if sin_sesion:
        sys.exit("Falta iniciar sesión de: " + ", ".join(sin_sesion) + ". Corré, una vez por persona:\n"
                 + "\n".join(f"    {plataforma().comando('agregar-usuario', u)}" for u in sin_sesion))
    base_cmd = [sys.executable, str(Path(__file__).resolve())]
    log(f"Lanzo la inscripción de {len(usuarios)} usuarios en paralelo: {', '.join(usuarios)}. Ctrl+C corta a todos.")
    hijos = []
    # Sin consola (arranque automático con pythonw) los hijos no tienen dónde escribir: sin esto, una tubería sin
    # lector se llena y los traba. Su salida igual queda en usuarios/<u>/estado/siu.log.
    salida = {} if sys.stdout else dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for u in usuarios:
        # "--" antes de los alias: uno que empiece con "-" no se confunde con una opción.
        cmd = [*base_cmd, "--usuario", u, "inscribir", "--intervalo", args.intervalo]
        cmd += ["--profesor", args.profesor] if args.profesor else []
        cmd += ["--simular"] if args.simular else []
        cmd += ["--repetir"] if args.repetir else []
        hijos.append((u, subprocess.Popen([*cmd, "--", *args.aliases], **salida)))
    codigos: dict[str, int] = {}
    try:
        for u, proceso in hijos:
            codigos[u] = proceso.wait()
    except KeyboardInterrupt:
        # Ctrl+C también llegó a los hijos (misma consola): les damos tiempo a cerrar su navegador y su lock.
        for u, proceso in hijos:
            try:
                codigos[u] = proceso.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proceso.terminate()
                codigos[u] = proceso.wait()
    log("Resumen: " + " · ".join(f"{u}: {'terminó' if c == 0 else f'falló (código {c})'}" for u, c in codigos.items())
        + ". El detalle de cada uno está arriba y en usuarios/<usuario>/estado/siu.log.")
    if any(codigos.values()):
        sys.exit(1)


def parser_inscribir(prog: str = "siu_notas.py inscribir") -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Espera (polling) a que la materia aparezca en Inscripción a materias con cupo y se anota "
                    "en la primera comisión disponible. Es lo único de este proyecto que escribe en el SIU.")
    ap.add_argument("aliases", nargs="+", help="nombres posibles de la MISMA materia (nombre, variantes, typos o código)")
    ap.add_argument("--profesor", default="", help="último recurso: si ningún alias aparece, busca materias de este docente")
    ap.add_argument("--simular", action="store_true", help="busca y dice qué haría, sin inscribirse")
    ap.add_argument("--intervalo", default="5-10", help="segundos entre consultas, 'min-max' al azar (por defecto 5-10)")
    ap.add_argument("--usuarios", default="", help="varios usuarios a la vez, ej. juani,mateo, o 'todos' (los de `usuarios`)")
    ap.add_argument("--repetir", action="store_true", help="vuelve a intentar aunque este pedido ya haya sido cumplido")
    return ap


def leer_intervalo(ap: argparse.ArgumentParser, texto: str) -> tuple[float, float]:
    try:
        extremos = [float(x) for x in texto.split("-")]
        minimo, maximo = extremos[0], extremos[-1]
        if len(extremos) > 2 or not 2 <= minimo <= maximo:
            raise ValueError
    except ValueError:
        ap.error("--intervalo tiene que ser 'min-max' en segundos, con min >= 2 (ej. 5-10)")
    return minimo, maximo


def cmd_inscribir(argv: list[str]) -> None:
    ap = parser_inscribir()
    args = ap.parse_args(argv)
    minimo, maximo = leer_intervalo(ap, args.intervalo)
    if args.usuarios:
        return inscribir_varios(args)
    clave = clave_pedido(args.aliases, args.profesor)
    cumplido = None if (args.simular or args.repetir) else pedido_cumplido(clave)
    if cumplido:
        log(f"Este pedido ya está cumplido ({cumplido['materia']}, {cumplido['fecha']}): no vuelvo a vigilar. "
            f"Para repetirlo: --repetir (o borrá {CUMPLIDAS.relative_to(BASE).as_posix()}).")
        return
    if inscripcion_en_curso():
        sys.exit("Ya hay un `inscribir` corriendo (estado/inscribiendo.lock). Cortalo antes de lanzar otro.")

    LOCK_INSCRIPCION.parent.mkdir(parents=True, exist_ok=True)
    LOCK_INSCRIPCION.write_text(str(os.getpid()))  # el PID permite distinguir un proceso vivo de un lock viejo
    log(f"Inscripción automática{' (SIMULACIÓN)' if args.simular else ''}: alias={args.aliases}"
        + (f", profesor='{args.profesor}'" if args.profesor else "") + f", cada {minimo:g}-{maximo:g} s. Ctrl+C para cortar.")
    cache: dict = {}
    previo, ultimo_log = "", 0.0
    errores = intentos_fallidos = 0
    try:
        with sync_playwright() as p:
            ctx = abrir_contexto(p, headless=True)
            page = ctx.new_page()
            try:
                if not ir_al_inicio(page):
                    sys.exit(f"Sesión vencida: corré `{comando_cli('login')}`.")
                elegir_carrera(page)
                while True:
                    LOCK_INSCRIPCION.touch()
                    motivo = ""
                    try:
                        objetivo, motivo = localizar_materia(page, args.aliases, args.profesor, cache)
                        if objetivo:
                            titulo, href = objetivo
                            page.goto(href, wait_until="networkidle")
                            comisiones = leer_comisiones(page, titulo)
                            materia = nombre_materia(titulo)
                            ya = next((c for c in comisiones if c["inscripto"]), None)
                            elegida = elegir_comision(comisiones)
                            if ya:
                                log(f"Ya estás inscripto en {materia}, comisión '{ya['comision']}'. No hago nada.")
                                if not args.simular:
                                    marcar_cumplido(clave, materia)
                                return
                            if not comisiones:
                                motivo = f"{materia} existe pero todavía no tiene comisiones"
                            elif not elegida:
                                motivo = f"{materia}: {len(comisiones)} comisión(es), ninguna con cupo"
                            elif args.simular:
                                boton = boton_inscripcion(page, elegida["indice"]).count()
                                cupo = "cupo sin definir" if elegida["quedan"] is None else f"quedan {elegida['quedan']}"
                                log(f"[SIMULACIÓN] Me anotaría en {materia}, comisión '{elegida['comision']}' "
                                    f"({cupo}, {elegida['inscriptos']} inscriptos). "
                                    f"Botón de inscripción: {'encontrado' if boton else 'NO ENCONTRADO'}. "
                                    f"Página guardada en {guardar_captura(page, 'simulacion')}")
                                return
                            else:
                                log(f"Hay cupo en {materia}, comisión '{elegida['comision']}': me inscribo.")
                                ok, mensaje = inscribir_en_comision(page, titulo, href, elegida)
                                if ok:
                                    log(f"Inscripto en {materia}, comisión '{elegida['comision']}'. SIU: {mensaje or '-'}")
                                    marcar_cumplido(clave, materia)
                                    avisar_inscripcion("Inscripto ✅", f"Te anoté en {materia}.",
                                                       f"Comisión {elegida['comision']}")
                                    return
                                intentos_fallidos += 1
                                log(f"No pude inscribirme ({intentos_fallidos}/{INTENTOS_FALLIDOS_PARA_RENDIRSE}): {mensaje}")
                                if intentos_fallidos >= INTENTOS_FALLIDOS_PARA_RENDIRSE:
                                    avisar_inscripcion(
                                        "No pude inscribirme", f"Había cupo en {materia} pero el SIU no me dejó anotarme.",
                                        mensaje)
                                    sys.exit(1)
                                motivo = "reintentando la inscripción"
                        errores = 0
                    except SesionCaida:
                        log("Se cayó la sesión; reintento entrar.")
                        if not ir_al_inicio(page):
                            avisar_inscripcion("Sesión caída", "Se cayó la sesión del SIU mientras esperaba la inscripción.",
                                               comando_cli("login"))
                            sys.exit(2)
                        elegir_carrera(page)
                        continue
                    except Ambiguo as e:
                        sys.exit(f"{e}")
                    except Exception as e:
                        errores += 1
                        log(f"Error pasajero {errores}/{ERRORES_PARA_RENDIRSE} ({type(e).__name__}: {str(e).splitlines()[0]}).")
                        if errores >= ERRORES_PARA_RENDIRSE:
                            avisar_inscripcion("Inscripción automática cortada",
                                               f"Fallaron {ERRORES_PARA_RENDIRSE} consultas seguidas al SIU.", str(e).splitlines()[0])
                            sys.exit(1)
                        LOCK_INSCRIPCION.touch()
                        time.sleep(ESPERA_ERROR_SEG)
                        continue
                    ahora = time.time()
                    if motivo and (motivo != previo or ahora - ultimo_log > LATIDO_SEG):
                        log(f"Esperando: {motivo}")
                        if motivo.startswith("ambiguo") and motivo != previo:
                            avisar_inscripcion("Inscripción: hace falta tu ayuda", "No sé cuál materia elegir.", motivo)
                        previo, ultimo_log = motivo, ahora
                    time.sleep(random.uniform(minimo, maximo))
            finally:
                ctx.close()
    except KeyboardInterrupt:
        log("Inscripción automática cancelada (Ctrl+C).")
    finally:
        LOCK_INSCRIPCION.unlink(missing_ok=True)


def cmd_check() -> None:
    estado = cargar_estado()
    with sync_playwright() as p:
        ctx = abrir_contexto(p, headless=True)
        page = ctx.new_page()
        try:
            if not ir_al_inicio(page):
                # Avisamos una sola vez hasta que se vuelva a loguear.
                if not estado.get("aviso_sesion_vencida"):
                    comando = comando_cli("login")
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
                    avisar_whatsapp("*SIU ▸ sesión caída*\nNo pude entrar al SIU: hay que volver a loguearse. "
                                    "Los pasos están en tu mail.")
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
    pendientes = lectura["pendientes"]
    log(f"examenes: {len(mesas)} mesas (materias en curso + {len(pendientes)} finales pendientes), "
        f"{len(nuevas_mesas)} nuevas.")
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
            "== Mesas de examen (materias en curso y finales pendientes) ==\n" + "\n".join(f"• {texto_examen(m)}" for m in examenes)
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

    # WhatsApp: por defecto solo el resumen (sin notas ni materias, porque pasa por CallMeBot).
    texto = f"*{prefijo}SIU ▸ novedades*\n" + "\n".join(f"• {r}" for r in resumen)
    if whatsapp.detalle_completo():
        detalle = [f for filas in novedades.values() for f in filas]
        detalle += [texto_examen(m) for m in examenes]
        detalle += [texto_inasistencias(c).splitlines()[0] for c in faltas]
        detalle += [texto_credito(c) for c in creditos]
        texto += "\n\n" + "\n".join(f"- {d}" for d in detalle)
    avisar_whatsapp(texto + "\n\nEl detalle está en tu mail.")


def avisar_whatsapp(texto: str) -> None:
    """Aviso complementario: si WhatsApp no está configurado no hace nada, y si falla
    solo queda en el log (el mail ya salió)."""
    if not whatsapp.configurado():
        return
    try:
        whatsapp.enviar(texto)
        log("WhatsApp enviado.")
    except Exception as e:
        log(f"No pude mandar el WhatsApp: {e}")


def cmd_test_mail() -> None:
    """Manda un mail de muestra con algunas notas reales ya vistas (o una de ejemplo)."""
    estado = cargar_estado()
    muestra = {k: estado[k][-3:] for k in TITULOS if estado.get(k)}
    if not muestra:
        muestra = {"en_curso": ["Materia de prueba (TEST): Parcial - Primer Parcial - 9.20 (Nueve con 20/100) Aprobado 15/09/2026"]}
    enviar_novedades(muestra, prefijo="[PRUEBA] ")  # sin créditos: requieren scrapear cursada


def cmd_test_whatsapp() -> None:
    if not whatsapp.configurado():
        sys.exit("Falta WHATSAPP_TELEFONO o WHATSAPP_APIKEY en .env (ver README -> Aviso por WhatsApp).")
    log(f"CallMeBot: {whatsapp.enviar('*SIU Notas* ✅ Prueba de WhatsApp: si te llegó esto, está todo bien.')}")


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
    if inscripcion_en_curso():
        # `inscribir` tiene abierto ./perfil: intentar abrirlo acá fallaría y contaría como error.
        log("Hay una inscripción automática en curso, salto esta revisión.")
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
                        comando_cli("check"),
                        INICIO,
                    ),
                )
            except Exception:
                log("Tampoco pude mandar el mail de error:\n" + traceback.format_exc())
                avisar_whatsapp(f"*SIU ▸ el monitor está fallando*\n{FALLOS_PARA_AVISAR} revisiones seguidas con error "
                                "y tampoco pude mandar el mail. Mirá estado/siu.log.")
                sys.exit(1)
            avisar_whatsapp(f"*SIU ▸ el monitor está fallando*\n{FALLOS_PARA_AVISAR} revisiones seguidas con error. "
                            "Más info en tu mail.")
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
    if USUARIO:
        sys.exit("La tarea programada es solo del usuario principal (sin --usuario).")
    intervalo = int(os.getenv("SIU_INTERVALO_MIN", "60"))
    cmd_verificar()
    log(f"Programando '{NOMBRE_TAREA}' cada {intervalo} min en {plataforma().nombre}.")
    log(plataforma().programar(NOMBRE_TAREA, intervalo))


def cmd_desprogramar() -> None:
    if USUARIO:
        sys.exit("La tarea programada es solo del usuario principal (sin --usuario).")
    plataforma().desprogramar(NOMBRE_TAREA)
    log(f"Tarea '{NOMBRE_TAREA}' eliminada.")


def _ultima_linea(archivo: Path) -> str:
    try:
        lineas = [l for l in archivo.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
        return lineas[-1][:110] if lineas else "-"
    except FileNotFoundError:
        return "-"


def cmd_autoinicio(argv: list[str]) -> None:
    """`autoinicio ALIAS...`: crea una tarea que lanza `inscribir` solo al iniciar sesión en la PC."""
    if USUARIO:
        sys.exit("autoinicio se usa sin --usuario: la tarea lanza a los usuarios con --usuarios.")
    ap = parser_inscribir("siu_notas.py autoinicio")
    args = ap.parse_args(argv)
    leer_intervalo(ap, args.intervalo)
    if args.simular:
        ap.error("--simular no tiene sentido en el arranque automático (es para probar a mano).")
    if args.usuarios.strip().lower() != "todos":
        nombres = [u.strip() for u in args.usuarios.split(",") if u.strip()]
        faltan = [u for u in nombres if not NOMBRE_USUARIO.fullmatch(u) or not tiene_sesion(u)]
        if faltan:
            sys.exit("Sin sesión (o nombre inválido): " + ", ".join(faltan) + ". Agregalos con `agregar-usuario NOMBRE`.")
    cmd_verificar()
    pedido = ["inscribir", "--intervalo", args.intervalo]
    pedido += ["--profesor", args.profesor] if args.profesor else []
    pedido += ["--usuarios", args.usuarios] if args.usuarios else []
    pedido += ["--", *args.aliases]
    log(plataforma().programar_al_iniciar(NOMBRE_TAREA_INSCRIPCION, pedido))
    log(f"Va a arrancar solo, 30 s después de iniciar sesión en Windows. Ahora mismo: `{comando_cli('autoinicio-iniciar')}`. "
        f"Ver: `{comando_cli('autoinicio-estado')}`. Quitar: `{comando_cli('autoinicio-quitar')}`.")
    if args.usuarios.strip().lower() == "todos":
        log("Con --usuarios todos, los usuarios que agregues después entran solos en el próximo arranque.")


def cmd_autoinicio_iniciar() -> None:
    if plataforma().estado_tarea(NOMBRE_TAREA_INSCRIPCION) == "no existe":
        sys.exit(f"No hay arranque automático. Creálo con `{comando_cli('autoinicio', 'ALIAS...', '--usuarios', 'todos')}`.")
    plataforma().iniciar_tarea(NOMBRE_TAREA_INSCRIPCION)
    log(f"Tarea '{NOMBRE_TAREA_INSCRIPCION}' iniciada. Ver: `{comando_cli('autoinicio-estado')}`.")


def cmd_autoinicio_estado() -> None:
    estado = plataforma().estado_tarea(NOMBRE_TAREA_INSCRIPCION)
    print(f"Tarea '{NOMBRE_TAREA_INSCRIPCION}': {estado}")
    if estado == "no existe":
        print(f"Creála con: {comando_cli('autoinicio', 'ALIAS...', '--usuarios', 'todos')}")
        return
    raices = [("(principal)", BASE)] + [(u, BASE / "usuarios" / u) for u in usuarios_existentes()]
    for nombre, raiz in raices:
        vivo = lock_vivo(raiz / "estado" / "inscribiendo.lock")
        if nombre == "(principal)" and not vivo:
            continue
        print(f"  {nombre:<12}{'vigilando' if vivo else 'parado':<11}{_ultima_linea(raiz / 'estado' / 'siu.log')}")


def cmd_autoinicio_quitar() -> None:
    """Quita el arranque automático y corta lo que esté vigilando (no depende de que el Programador mate a los hijos)."""
    existe = plataforma().estado_tarea(NOMBRE_TAREA_INSCRIPCION) != "no existe"
    if existe:
        plataforma().detener_tarea(NOMBRE_TAREA_INSCRIPCION)
        plataforma().desprogramar(NOMBRE_TAREA_INSCRIPCION)
    cortados = []
    for nombre, raiz in [("(principal)", BASE)] + [(u, BASE / "usuarios" / u) for u in usuarios_existentes()]:
        lock = raiz / "estado" / "inscribiendo.lock"
        if lock_vivo(lock):
            try:
                os.kill(int(lock.read_text().strip()), signal.SIGTERM)  # en Windows termina el proceso
                cortados.append(nombre)
            except (ValueError, OSError):
                pass
            lock.unlink(missing_ok=True)
    log(("Arranque automático eliminado. " if existe else "No había arranque automático. ")
        + (f"Corté la vigilancia de: {', '.join(cortados)}." if cortados else "No había nada vigilando."))


if __name__ == "__main__":
    comandos = {
        "login": cmd_login,
        "cookies": cmd_cookies,
        "usuarios": cmd_usuarios,
        "autoinicio-iniciar": cmd_autoinicio_iniciar,
        "autoinicio-estado": cmd_autoinicio_estado,
        "autoinicio-quitar": cmd_autoinicio_quitar,
        "check": check_con_aviso_de_error,
        "dump": cmd_dump,
        "test-mail": cmd_test_mail,
        "test-whatsapp": cmd_test_whatsapp,
        "verificar": cmd_verificar,
        "programar": cmd_programar,
        "desprogramar": cmd_desprogramar,
    }
    con_argumentos = {"inscribir": cmd_inscribir, "agregar-usuario": cmd_agregar_usuario, "autoinicio": cmd_autoinicio}
    if len(sys.argv) >= 2 and sys.argv[1] in con_argumentos:
        con_argumentos[sys.argv[1]](sys.argv[2:])
    elif len(sys.argv) != 2 or sys.argv[1] not in comandos:
        sys.exit(f"Uso: python siu_notas.py [{'|'.join([*comandos, *con_argumentos])}]")
    else:
        comandos[sys.argv[1]]()
