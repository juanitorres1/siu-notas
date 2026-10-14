"""HTML del mail de novedades, estética futurista/neón.

Los clientes de mail (Gmail sobre todo) ignoran fuentes web, <style> parcial y
CSS moderno: todo va en tablas con estilos inline, y cada gradiente tiene un
bgcolor plano de respaldo.
"""

import re
from datetime import datetime
from html import escape

FONDO = "#05060f"
PANEL = "#0b1024"
BORDE = "#1c2550"
TEXTO = "#e6ebff"
TENUE = "#7b86b8"
CIAN = "#00f0ff"
MAGENTA = "#ff2bd6"
VERDE = "#39ff9f"
AMBAR = "#ffb636"
ROJO = "#ff4d6d"
MONO = "'Consolas','SFMono-Regular','Menlo','Courier New',monospace"
SANS = "'Segoe UI','Helvetica Neue',Arial,sans-serif"

ICONOS = {"en_curso": "◇", "actuacion": "◈", "historia": "◆"}


def _partes(fila: str) -> dict:
    """'Materia (COD): Tipo - Desc - 9.20 (Nueve...) Aprobado 15/09/2026' -> campos."""
    materia, _, detalle = fila.partition(": ")
    codigo = ""
    m = re.match(r"(.*?)\s*\(([^()]+)\)$", materia)
    if m:
        materia, codigo = m.group(1), m.group(2)
    nota = re.search(r"\b(\d{1,2}(?:\.\d{1,2})?)\s*\(", detalle)
    fecha = re.search(r"\d{2}/\d{2}/\d{4}", detalle)
    resultado = re.search(r"\b(Promocionado|Aprobado|Desaprobado|Ausente|Libre|Reprobado)\b", detalle)
    # Lo que queda antes de la nota/resultado es el tipo y la descripción.
    corte = min(x.start() for x in (nota, resultado, fecha) if x) if (nota or resultado or fecha) else len(detalle)
    titulo = detalle[:corte].strip(" -")
    return {
        "materia": materia.strip() or fila,
        "codigo": codigo,
        "titulo": titulo,
        "nota": nota.group(1) if nota else "",
        "resultado": resultado.group(1) if resultado else "",
        "fecha": fecha.group(0) if fecha else "",
    }


def _color_nota(nota: str, resultado: str) -> str:
    if resultado in ("Desaprobado", "Ausente", "Libre", "Reprobado"):
        return ROJO
    try:
        n = float(nota)
    except ValueError:
        return CIAN
    if n >= 8:
        return VERDE
    if n >= 6:
        return CIAN
    if n >= 4:
        return AMBAR
    return ROJO


def _tarjeta(fila: str) -> str:
    p = _partes(fila)
    color = _color_nota(p["nota"], p["resultado"])
    nota = escape(p["nota"].removesuffix(".00")) if p["nota"] else "✓"
    meta = " · ".join(escape(x) for x in (p["resultado"], p["fecha"]) if x)
    codigo = f'<span style="color:{TENUE};font-family:{MONO};font-size:11px;letter-spacing:1px;">{escape(p["codigo"])}</span>' if p["codigo"] else ""
    return f"""
<tr><td style="padding:0 0 12px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{PANEL}"
         style="background:{PANEL};border:1px solid {BORDE};border-left:3px solid {color};border-radius:10px;">
    <tr>
      <td style="padding:16px 18px;vertical-align:middle;">
        {codigo}
        <div style="color:{TEXTO};font-family:{SANS};font-size:16px;font-weight:600;line-height:1.3;margin-top:2px;">{escape(p["materia"])}</div>
        <div style="color:{TENUE};font-family:{SANS};font-size:13px;margin-top:4px;">{escape(p["titulo"])}</div>
        <div style="color:{color};font-family:{MONO};font-size:11px;letter-spacing:1.5px;text-transform:uppercase;margin-top:8px;">▸ {meta}</div>
      </td>
      <td width="84" align="center" style="padding:12px 16px 12px 0;vertical-align:middle;">
        <table role="presentation" cellpadding="0" cellspacing="0" style="border:2px solid {color};border-radius:12px;">
          <tr><td align="center" style="padding:10px 12px;min-width:44px;color:{color};font-family:{MONO};font-size:26px;font-weight:700;line-height:1;text-shadow:0 0 12px {color};">{nota}</td></tr>
        </table>
      </td>
    </tr>
  </table>
</td></tr>"""


def _seccion(clave: str, titulo: str, filas: list[str]) -> str:
    return _encabezado_seccion(ICONOS.get(clave, "◇"), titulo, len(filas)) + "".join(_tarjeta(f) for f in filas)


def _marco(preheader: str, cuerpo: str, url: str, boton: str) -> str:
    ahora = datetime.now().strftime("%d.%m.%Y // %H:%M")
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark">
<title>SIU // Notas</title></head>
<body style="margin:0;padding:0;background:{FONDO};" bgcolor="{FONDO}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{FONDO}" style="background:{FONDO};">
<tr><td align="center" style="padding:28px 14px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px;">

    <!-- barra superior -->
    <tr><td style="height:3px;line-height:3px;font-size:3px;background:{CIAN};background-image:linear-gradient(90deg,{CIAN},{MAGENTA});" bgcolor="{CIAN}">&nbsp;</td></tr>

    <!-- header -->
    <tr><td bgcolor="{PANEL}" style="background:{PANEL};background-image:linear-gradient(135deg,#0d1433 0%,#140a2e 100%);padding:26px 24px 22px 24px;border:1px solid {BORDE};border-top:0;">
      <div style="color:{TENUE};font-family:{MONO};font-size:11px;letter-spacing:3px;">SIU://AUSTRAL &nbsp;·&nbsp; {ahora}</div>
      <div style="color:{TEXTO};font-family:{MONO};font-size:28px;font-weight:700;letter-spacing:2px;margin-top:10px;text-shadow:0 0 18px {CIAN};">
        NUEVA<span style="color:{CIAN};">_</span>TRANSMISIÓN
      </div>
      <div style="color:{MAGENTA};font-family:{MONO};font-size:12px;letter-spacing:2px;margin-top:8px;">▲ {escape(preheader.upper())}</div>
    </td></tr>

    <tr><td style="padding:4px 0 0 0;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{cuerpo}</table>
    </td></tr>

    <!-- CTA -->
    <tr><td align="center" style="padding:20px 0 8px 0;">
      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td bgcolor="{FONDO}" style="border:1px solid {CIAN};border-radius:8px;box-shadow:0 0 16px rgba(0,240,255,.35);">
          <a href="{escape(url)}" style="display:inline-block;padding:13px 28px;color:{CIAN};font-family:{MONO};font-size:13px;font-weight:700;letter-spacing:3px;text-decoration:none;">{escape(boton)} &rarr;</a>
        </td>
      </tr></table>
    </td></tr>

    <!-- footer -->
    <tr><td align="center" style="padding:22px 0 0 0;color:#3d4672;font-family:{MONO};font-size:10px;letter-spacing:2px;">
      ░░ MONITOR AUTÓNOMO // SIU-NOTAS v1 ░░
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


def _barra_cupo(c: dict) -> str:
    if c.get("cupo") is None:
        return f'<div style="color:{TENUE};font-family:{MONO};font-size:11px;letter-spacing:1.5px;margin-top:10px;">CUPO S/D</div>'
    total, quedan = c["cupo"], c["quedan"]
    ocupado = 100 if total == 0 else round(100 * (total - quedan) / total)
    color = ROJO if quedan == 0 else AMBAR if quedan <= max(2, total * 0.1) else VERDE
    etiqueta = "SIN CUPO" if quedan == 0 else f"QUEDAN {quedan} DE {total}"
    libre = 100 - ocupado
    celda_libre = f'<td width="{libre}%" bgcolor="{BORDE}" style="background:{BORDE};height:6px;line-height:6px;font-size:1px;">&nbsp;</td>' if libre else ""
    celda_ocup = f'<td width="{ocupado}%" bgcolor="{color}" style="background:{color};height:6px;line-height:6px;font-size:1px;">&nbsp;</td>' if ocupado else ""
    return f"""
        <div style="color:{color};font-family:{MONO};font-size:11px;letter-spacing:1.5px;margin-top:12px;">▸ {etiqueta}</div>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:6px;border-radius:3px;overflow:hidden;">
          <tr>{celda_ocup}{celda_libre}</tr>
        </table>"""


def _tarjeta_credito(c: dict) -> str:
    materia = c["materia"]
    codigo = ""
    m = re.match(r"(.*?)\s*\(([^()]+)\)$", materia)
    if m:
        materia, codigo = m.group(1), m.group(2)
    creditos = c["creditos"].removesuffix(".0")
    detalles = []
    if c.get("cuando"):
        detalles.append(f"◷ {escape(c['cuando'])}")
    if c.get("asistencia"):
        detalles.append(f"◉ Asistencia {escape(c['asistencia'])}%")
    if c.get("inscripto"):
        detalles.append(f'<span style="color:{VERDE};">✓ Ya estás inscripto</span>')
    lineas = "".join(
        f'<div style="color:{TENUE};font-family:{SANS};font-size:13px;line-height:1.45;margin-top:4px;">{d}</div>'
        for d in detalles
    )
    codigo_html = f'<span style="color:{TENUE};font-family:{MONO};font-size:11px;letter-spacing:1px;">{escape(codigo)}</span>' if codigo else ""
    return f"""
<tr><td style="padding:0 0 12px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{PANEL}"
         style="background:{PANEL};border:1px solid {BORDE};border-left:3px solid {MAGENTA};border-radius:10px;">
    <tr>
      <td style="padding:16px 18px;vertical-align:top;">
        {codigo_html}
        <div style="color:{TEXTO};font-family:{SANS};font-size:16px;font-weight:600;line-height:1.3;margin-top:2px;">{escape(materia)}</div>
        <div style="color:{CIAN};font-family:{SANS};font-size:13px;margin-top:4px;">{escape(c['comision'])}</div>
        {lineas}
        {_barra_cupo(c)}
      </td>
      <td width="84" align="center" style="padding:16px 16px 12px 0;vertical-align:top;">
        <table role="presentation" cellpadding="0" cellspacing="0" style="border:2px solid {MAGENTA};border-radius:12px;">
          <tr><td align="center" style="padding:10px 12px 4px 12px;min-width:44px;color:{MAGENTA};font-family:{MONO};font-size:26px;font-weight:700;line-height:1;text-shadow:0 0 12px {MAGENTA};">+{escape(creditos)}</td></tr>
          <tr><td align="center" style="padding:0 8px 8px 8px;color:{MAGENTA};font-family:{MONO};font-size:9px;letter-spacing:2px;">CRÉD.</td></tr>
        </table>
      </td>
    </tr>
  </table>
</td></tr>"""


def _encabezado_seccion(icono: str, titulo: str, n: int) -> str:
    return f"""
<tr><td style="padding:22px 0 12px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
    <td style="color:{CIAN};font-family:{MONO};font-size:12px;letter-spacing:3px;text-transform:uppercase;white-space:nowrap;">
      {icono}&nbsp; {escape(titulo)} <span style="color:{MAGENTA};">[{n:02d}]</span>
    </td>
    <td width="100%" style="padding-left:12px;"><div style="height:1px;background:{BORDE};line-height:1px;font-size:1px;">&nbsp;</div></td>
  </tr></table>
</td></tr>"""


COLOR_ACCION = {"nueva": ROJO, "quitada": VERDE, "modificada": CIAN}


def _tarjeta_faltas(m: dict) -> str:
    antes, ahora = float(m["antes"]), float(m["ahora"])
    color = ROJO if ahora > antes else VERDE if ahora < antes else CIAN
    flecha = "▲" if ahora > antes else "▼" if ahora < antes else "="
    lineas = "".join(
        f'<div style="color:{TENUE};font-family:{SANS};font-size:13px;line-height:1.45;margin-top:4px;">'
        f'<span style="color:{COLOR_ACCION.get(c["accion"], TENUE)};font-family:{MONO};font-size:11px;letter-spacing:1px;text-transform:uppercase;">{escape(c["accion"])}</span>'
        f' &nbsp;{escape(c["fecha"])} · {escape(c["tipo"])} · {escape(c["detalle"])}</div>'
        for c in m["cambios"]
    )
    return f"""
<tr><td style="padding:0 0 12px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{PANEL}"
         style="background:{PANEL};border:1px solid {BORDE};border-left:3px solid {color};border-radius:10px;">
    <tr>
      <td style="padding:16px 18px;vertical-align:top;">
        <div style="color:{TEXTO};font-family:{SANS};font-size:16px;font-weight:600;line-height:1.3;">{escape(m["materia"])}</div>
        <div style="color:{color};font-family:{MONO};font-size:11px;letter-spacing:1.5px;margin-top:6px;">▸ INASISTENCIAS {escape(m["antes"])} {flecha} {escape(m["ahora"])}</div>
        {lineas}
      </td>
      <td width="84" align="center" style="padding:16px 16px 12px 0;vertical-align:top;">
        <table role="presentation" cellpadding="0" cellspacing="0" style="border:2px solid {color};border-radius:12px;">
          <tr><td align="center" style="padding:10px 12px 4px 12px;min-width:44px;color:{color};font-family:{MONO};font-size:26px;font-weight:700;line-height:1;text-shadow:0 0 12px {color};">{escape(m["ahora"])}</td></tr>
          <tr><td align="center" style="padding:0 8px 8px 8px;color:{color};font-family:{MONO};font-size:9px;letter-spacing:2px;">FALTAS</td></tr>
        </table>
      </td>
    </tr>
  </table>
</td></tr>"""


MESES = ["ENE", "FEB", "MAR", "ABR", "MAY", "JUN", "JUL", "AGO", "SEP", "OCT", "NOV", "DIC"]


def _tarjeta_examen(m: dict) -> str:
    fecha = re.match(r"(\d{2})/(\d{2})/(\d{4})(?:\s+(\d{1,2}:\d{2}))?", m["fecha"])
    if fecha:
        dia, mes, hora = fecha.group(1), MESES[int(fecha.group(2)) - 1], fecha.group(4) or ""
    else:
        dia, mes, hora = "?", "", m["fecha"]
    detalles = []
    for icono, valor in (("◷", m.get("turno")), ("◈", m.get("tipo")),
                         ("⌖", " - ".join(x for x in (m.get("ubicacion"), m.get("lugar")) if x)),
                         ("◉", m.get("docentes"))):
        if valor:
            detalles.append(f"{icono} {escape(valor)}")
    if m.get("inscripto"):
        detalles.append(f'<span style="color:{VERDE};">✓ Ya estás inscripto</span>')
    lineas = "".join(
        f'<div style="color:{TENUE};font-family:{SANS};font-size:13px;line-height:1.45;margin-top:4px;">{d}</div>'
        for d in detalles
    )
    return f"""
<tr><td style="padding:0 0 12px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{PANEL}"
         style="background:{PANEL};border:1px solid {BORDE};border-left:3px solid {AMBAR};border-radius:10px;">
    <tr>
      <td style="padding:16px 18px;vertical-align:top;">
        <div style="color:{TEXTO};font-family:{SANS};font-size:16px;font-weight:600;line-height:1.3;">{escape(m["materia"])}</div>
        <div style="color:{AMBAR};font-family:{MONO};font-size:11px;letter-spacing:1.5px;margin-top:6px;">▸ {escape(m["fecha"])}</div>
        {lineas}
      </td>
      <td width="84" align="center" style="padding:16px 16px 12px 0;vertical-align:top;">
        <table role="presentation" cellpadding="0" cellspacing="0" style="border:2px solid {AMBAR};border-radius:12px;">
          <tr><td align="center" style="padding:8px 12px 0 12px;color:{AMBAR};font-family:{MONO};font-size:10px;letter-spacing:2px;">{mes}</td></tr>
          <tr><td align="center" style="padding:2px 12px;min-width:44px;color:{AMBAR};font-family:{MONO};font-size:26px;font-weight:700;line-height:1;text-shadow:0 0 12px {AMBAR};">{dia}</td></tr>
          <tr><td align="center" style="padding:0 8px 8px 8px;color:{AMBAR};font-family:{MONO};font-size:10px;letter-spacing:1px;">{escape(hora)}</td></tr>
        </table>
      </td>
    </tr>
  </table>
</td></tr>"""


def html_novedades(
    novedades: dict[str, list[str]],
    titulos: dict[str, str],
    url: str,
    creditos: list[dict] | None = None,
    url_cursada: str | None = None,
    faltas: list[dict] | None = None,
    examenes: list[dict] | None = None,
) -> str:
    creditos, faltas, examenes = creditos or [], faltas or [], examenes or []
    total = sum(len(v) for v in novedades.values())
    partes = []
    if total:
        partes.append(f"{total} nota{'s' if total != 1 else ''} nueva{'s' if total != 1 else ''}")
    if examenes:
        partes.append(f"{len(examenes)} mesa{'s' if len(examenes) != 1 else ''} de examen")
    if creditos:
        partes.append(f"{len(creditos)} actividad{'es' if len(creditos) != 1 else ''} con créditos")
    if faltas:
        partes.append(f"inasistencias en {len(faltas)} materia{'s' if len(faltas) != 1 else ''}")
    cuerpo = "".join(_seccion(k, titulos.get(k, k), filas) for k, filas in novedades.items())
    if examenes:
        cuerpo += _encabezado_seccion("▣", "Mesas de examen", len(examenes))
        cuerpo += (
            f'<tr><td style="padding:0 0 12px 0;color:{TENUE};font-family:{SANS};font-size:12px;">'
            "De tus materias en curso · adjunto <span style=\"color:" + AMBAR + ";\">examenes.ics</span> para tu calendario</td></tr>"
        )
        cuerpo += "".join(_tarjeta_examen(m) for m in examenes)
    if faltas:
        cuerpo += _encabezado_seccion("◎", "Inasistencias", len(faltas))
        cuerpo += "".join(_tarjeta_faltas(m) for m in faltas)
    if creditos:
        cuerpo += _encabezado_seccion("✦", "Actividades con créditos", len(creditos))
        cuerpo += "".join(_tarjeta_credito(c) for c in creditos)
    # Si solo hay créditos, el botón lleva directo a Inscripción a materias.
    solo_creditos = creditos and not total and not faltas and not examenes and url_cursada
    destino, boton = (url_cursada, "IR A INSCRIPCIÓN") if solo_creditos else (url, "ABRIR SIU")
    return _marco(" · ".join(partes) or "sin novedades", cuerpo, destino, boton)


def html_aviso(titulo: str, mensaje: str, comando: str, url: str) -> str:
    cuerpo = f"""
<tr><td style="padding:22px 0 0 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{PANEL}"
         style="background:{PANEL};border:1px solid {BORDE};border-left:3px solid {AMBAR};border-radius:10px;">
    <tr><td style="padding:18px;">
      <div style="color:{AMBAR};font-family:{MONO};font-size:12px;letter-spacing:3px;">⚠ {escape(titulo.upper())}</div>
      <div style="color:{TEXTO};font-family:{SANS};font-size:14px;line-height:1.5;margin-top:10px;">{escape(mensaje)}</div>
      <div style="margin-top:14px;padding:12px 14px;background:{FONDO};border:1px solid {BORDE};border-radius:6px;color:{VERDE};font-family:{MONO};font-size:13px;">$ {escape(comando)}</div>
    </td></tr>
  </table>
</td></tr>"""
    return _marco(titulo, cuerpo, url, "IR AL SIU")
