"""HTML del mail con estética formal tipo SIU Guaraní (Bootstrap 2 de la Austral).

Misma API que mail_ui (html_novedades / html_aviso). Tablas + estilos inline
porque Gmail ignora casi todo el CSS de <style>.
"""

from datetime import datetime
from html import escape

from mail_ui import _partes

# Sin logo ni marca de la universidad: el Reglamento General de Estudiantes de la Austral
# (Otras conductas, inc. t) pide usar su nombre, símbolos y logos solo con permiso.
# El encabezado es texto propio ("SIU Notas").


FONDO = "#eeeeee"
BLANCO = "#ffffff"
GRIS_HEADER = "#f7f7f7"
BORDE = "#dddddd"
TEXTO = "#333333"
TENUE = "#888888"
AZUL = "#0088cc"        # títulos ("Historia académica")
MARINO = "#2e2a7f"      # pestaña activa del menú / título
BARRA_MATERIA = "#4b7db5"  # barra de cada materia en la historia completa
NARANJA = "#faa732"     # progress bar-warning
FONT = "'Helvetica Neue',Helvetica,Arial,sans-serif"

ETIQUETAS = {  # colores de las .label de Bootstrap 2
    "Promocionado": "#468847",
    "Aprobado": "#468847",
    "Desaprobado": "#b94a48",
    "Reprobado": "#b94a48",
    "Ausente": "#999999",
    "Libre": "#999999",
}


def _etiqueta(resultado: str) -> str:
    if not resultado:
        return ""
    color = ETIQUETAS.get(resultado, "#3a87ad")
    return (
        f'<span style="display:inline-block;padding:2px 6px;background:{color};color:{BLANCO};'
        f'font-family:{FONT};font-size:11px;font-weight:bold;border-radius:3px;">{escape(resultado)}</span>'
    )


def _titulo_seccion(texto: str) -> str:
    # Como los h2 grises de Actuación provisoria ("Cursadas", "Exámenes").
    return f"""
<tr><td style="padding:18px 0 8px 0;color:#999999;font-family:{FONT};font-size:20px;font-weight:bold;">{escape(texto)}</td></tr>"""


def _bloque_notas(titulo: str, filas: list[str]) -> str:
    # Agrupa por materia como la Historia académica: barra azul + una línea por nota.
    por_materia: dict[str, list[dict]] = {}
    for fila in filas:
        p = _partes(fila)
        nombre = f"{p['materia']} ({p['codigo']})" if p["codigo"] else p["materia"]
        por_materia.setdefault(nombre, []).append(p)
    html = _titulo_seccion(titulo)
    for materia, notas in por_materia.items():
        lineas = ""
        for p in notas:
            tipo, _, desc = p["titulo"].partition(" - ")
            desc_html = f" - {escape(desc)}" if desc else ""
            nota = f" - <strong>{escape(p['nota'])}</strong>" if p["nota"] else ""
            fecha = f' <span style="color:{TENUE};">{escape(p["fecha"])}</span>' if p["fecha"] else ""
            lineas += f"""
      <tr><td style="padding:7px 10px;border-top:1px solid #eeeeee;color:{TEXTO};font-family:{FONT};font-size:13px;line-height:1.5;">
        <strong>{escape(tipo)}</strong>{desc_html}{nota} &nbsp;{_etiqueta(p['resultado'])}{fecha}
      </td></tr>"""
        html += f"""
<tr><td style="padding:0 0 10px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDE};">
    <tr><td bgcolor="{BARRA_MATERIA}" style="background:{BARRA_MATERIA};padding:6px 10px;color:{BLANCO};font-family:{FONT};font-size:14px;font-weight:bold;">{escape(materia)}</td></tr>{lineas}
  </table>
</td></tr>"""
    return html


def _fila_dato(clave: str, valor: str) -> str:
    return f"""
      <tr>
        <td width="30%" valign="top" style="padding:5px 0;color:{TEXTO};font-family:{FONT};font-size:13px;">{escape(clave)}:</td>
        <td valign="top" style="padding:5px 0;color:{TEXTO};font-family:{FONT};font-size:13px;">{valor}</td>
      </tr>"""


def _bloque_creditos(creditos: list[dict]) -> str:
    html = _titulo_seccion("Actividades con créditos")
    for c in creditos:
        datos = _fila_dato("Créditos", f"<strong>{escape(c['creditos'].removesuffix('.0'))}</strong>")
        if c.get("cuando"):
            datos += _fila_dato("Días", escape(c["cuando"]))
        if c.get("asistencia"):
            datos += _fila_dato("Asistencia", f"{escape(c['asistencia'])}%")
        if c.get("cupo") is not None:
            ocupado = 100 if c["cupo"] == 0 else round(100 * c["inscriptos"] / c["cupo"])
            ocupado = min(ocupado, 100)
            libre = 100 - ocupado
            color = "#dd514c" if c["quedan"] == 0 else NARANJA if ocupado >= 80 else "#5eb95e"
            barra = (
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin-top:6px;border:1px solid {BORDE};">'
                f"<tr>"
                + (f'<td width="{ocupado}%" bgcolor="{color}" style="background:{color};padding:2px 6px;color:{TEXTO};font-family:{FONT};font-size:11px;font-weight:bold;">{ocupado}%</td>' if ocupado else "")
                + (f'<td width="{libre}%" bgcolor="#f5f5f5" style="background:#f5f5f5;font-size:11px;">&nbsp;</td>' if libre else "")
                + "</tr></table>"
            )
            quedan = "sin cupo" if c["quedan"] == 0 else f"queda{'n' if c['quedan'] != 1 else ''} {c['quedan']}"
            datos += _fila_dato(
                "Cupo", f"{c['cupo']} | Inscriptos: {c['inscriptos']} &nbsp;<strong>({quedan})</strong>{barra}"
            )
        if c.get("inscripto"):
            datos += _fila_dato("Estado", _etiqueta("Aprobado").replace("Aprobado", "Ya estás inscripto"))
        html += f"""
<tr><td style="padding:0 0 12px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDE};">
    <tr><td style="padding:10px 12px 4px 12px;color:#222222;font-family:{FONT};font-size:16px;font-weight:bold;">{escape(c['materia'])}</td></tr>
    <tr><td style="padding:2px 12px 6px 12px;color:#222222;font-family:{FONT};font-size:13px;font-weight:bold;">&#10132; Comisión: {escape(c['comision'])}</td></tr>
    <tr><td style="padding:0 12px 10px 12px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{datos}</table>
    </td></tr>
  </table>
</td></tr>"""
    return html


ACCIONES = {  # etiqueta y color por tipo de cambio en inasistencias
    "nueva": ("Nueva", "#b94a48"),
    "quitada": ("Quitada", "#468847"),
    "modificada": ("Modificada", "#3a87ad"),
}


def _bloque_faltas(faltas: list[dict]) -> str:
    html = _titulo_seccion("Inasistencias")
    for m in faltas:
        subio = float(m["ahora"]) > float(m["antes"])
        bajo = float(m["ahora"]) < float(m["antes"])
        flecha = "&#9650;" if subio else "&#9660;" if bajo else "="
        color_total = "#f2dede" if subio else "#dff0d8" if bajo else "#d9edf7"
        lineas = ""
        for c in m["cambios"]:
            texto, color = ACCIONES.get(c["accion"], (c["accion"].title(), "#999999"))
            etiqueta = (
                f'<span style="display:inline-block;padding:2px 6px;background:{color};color:{BLANCO};'
                f'font-family:{FONT};font-size:11px;font-weight:bold;border-radius:3px;">{escape(texto)}</span>'
            )
            lineas += f"""
      <tr>
        <td width="22%" style="padding:7px 10px;border-top:1px solid #eeeeee;color:{TEXTO};font-family:{FONT};font-size:13px;">{escape(c['fecha'])}</td>
        <td style="padding:7px 10px;border-top:1px solid #eeeeee;color:{TEXTO};font-family:{FONT};font-size:13px;">
          {etiqueta} &nbsp;{escape(c['tipo'])} <span style="color:{TENUE};">· {escape(c['detalle'])}</span>
        </td>
      </tr>"""
        html += f"""
<tr><td style="padding:0 0 10px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDE};">
    <tr><td colspan="2" bgcolor="{BARRA_MATERIA}" style="background:{BARRA_MATERIA};padding:6px 10px;color:{BLANCO};font-family:{FONT};font-size:14px;font-weight:bold;">{escape(m['materia'])}</td></tr>
    <tr><td colspan="2" bgcolor="{color_total}" style="background:{color_total};padding:7px 10px;color:{TEXTO};font-family:{FONT};font-size:13px;">
      Total de inasistencias: <strong>{escape(m['antes'])} {flecha} {escape(m['ahora'])}</strong>
    </td></tr>{lineas}
  </table>
</td></tr>"""
    return html


def _bloque_examenes(mesas: list[dict]) -> str:
    html = _titulo_seccion("Mesas de examen")
    html += f"""
<tr><td style="padding:0 0 10px 0;color:{TENUE};font-family:{FONT};font-size:12px;">
  De tus materias en curso y finales pendientes. Adjunto va <strong>examenes.ics</strong>: abrilo para agregarlas a tu calendario.
</td></tr>"""
    for m in mesas:
        datos = ""
        for clave, valor in (
            ("Motivo", m.get("motivo")),
            ("Turno", m.get("turno")), ("Llamado", m.get("llamado")), ("Tipo de inscripción", m.get("tipo")),
            ("Ubicación", " - ".join(x for x in (m.get("ubicacion"), m.get("lugar")) if x)), ("Docentes", m.get("docentes")),
        ):
            if valor:
                datos += _fila_dato(clave, escape(valor))
        if m.get("inscripto"):
            datos += _fila_dato("Estado", _etiqueta("Aprobado").replace("Aprobado", "Ya estás inscripto"))
        html += f"""
<tr><td style="padding:0 0 12px 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border:1px solid {BORDE};">
    <tr><td bgcolor="{BARRA_MATERIA}" style="background:{BARRA_MATERIA};padding:6px 10px;color:{BLANCO};font-family:{FONT};font-size:14px;font-weight:bold;">{escape(m['materia'])}</td></tr>
    <tr><td style="padding:10px 12px 2px 12px;color:#222222;font-family:{FONT};font-size:15px;font-weight:bold;">&#128197; {escape(m['fecha'])}</td></tr>
    <tr><td style="padding:0 12px 10px 12px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">{datos}</table>
    </td></tr>
  </table>
</td></tr>"""
    return html


def _boton(url: str, texto: str) -> str:
    return f"""
<tr><td align="right" style="padding:6px 0 4px 0;">
  <table role="presentation" cellpadding="0" cellspacing="0"><tr>
    <td bgcolor="#f5f5f5" style="background:#f5f5f5;background-image:linear-gradient(#ffffff,#e6e6e6);border:1px solid #cccccc;border-bottom-color:#b3b3b3;border-radius:4px;">
      <a href="{escape(url)}" style="display:inline-block;padding:6px 14px;color:{TEXTO};font-family:{FONT};font-size:14px;text-decoration:none;">{escape(texto)}</a>
    </td>
  </tr></table>
</td></tr>"""


def _marco(titulo: str, preheader: str, cuerpo: str, pestana: str) -> str:
    fecha = datetime.now().strftime("%d/%m/%Y %H:%M")
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light">
<title>SIU Guaraní</title></head>
<body style="margin:0;padding:0;background:{FONDO};" bgcolor="{FONDO}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{escape(preheader)}</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{FONDO}" style="background:{FONDO};">
<tr><td align="center" style="padding:20px 10px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{BLANCO}" style="max-width:640px;background:{BLANCO};border:1px solid {BORDE};">

    <!-- encabezado -->
    <tr><td bgcolor="{GRIS_HEADER}" style="background:{GRIS_HEADER};padding:14px 18px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td valign="middle">
          <div style="color:{MARINO};font-family:{FONT};font-size:22px;font-weight:bold;letter-spacing:.5px;">SIU Notas</div>
          <div style="color:#777777;font-family:{FONT};font-size:11px;letter-spacing:1px;text-transform:uppercase;margin-top:2px;">Monitor personal del SIU Guaraní · no oficial</div>
        </td>
        <td align="right" valign="middle" style="color:#666666;font-family:{FONT};font-size:12px;">{fecha}</td>
      </tr></table>
    </td></tr>

    <!-- menú -->
    <tr><td style="border-top:1px solid {BORDE};border-bottom:1px solid {BORDE};background:#f2f2f2;background-image:linear-gradient(#ffffff,#f2f2f2);" bgcolor="#f2f2f2">
      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td bgcolor="{MARINO}" style="background:{MARINO};padding:10px 15px;color:{BLANCO};font-family:{FONT};font-size:13px;">{escape(pestana)}</td>
        <td style="padding:10px 15px;color:#777777;font-family:{FONT};font-size:13px;border-right:1px solid #e5e5e5;">SIU Guaraní · Autogestión</td>
      </tr></table>
    </td></tr>

    <!-- contenido -->
    <tr><td style="padding:18px 18px 8px 18px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        <tr><td style="padding:0 0 6px 0;border-bottom:1px solid #eeeeee;color:{AZUL};font-family:{FONT};font-size:22px;font-weight:bold;">{escape(titulo)}</td></tr>
        <tr><td style="padding:10px 0 0 0;color:{TENUE};font-family:{FONT};font-size:13px;">{escape(preheader)}.</td></tr>
        {cuerpo}
      </table>
    </td></tr>

    <!-- pie -->
    <tr><td style="padding:12px 18px;border-top:1px solid #eeeeee;color:#999999;font-family:{FONT};font-size:11px;">
      Mensaje generado automáticamente por tu monitor de notas del SIU Guaraní. No responder.
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


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
        partes.append(f"cambios de inasistencias en {len(faltas)} materia{'s' if len(faltas) != 1 else ''}")
    cuerpo = "".join(_bloque_notas(titulos.get(k, k), filas) for k, filas in novedades.items())
    if examenes:
        cuerpo += _bloque_examenes(examenes)
    if faltas:
        cuerpo += _bloque_faltas(faltas)
    if creditos:
        cuerpo += _bloque_creditos(creditos)
    solo_creditos = creditos and not total and not faltas and not examenes and url_cursada
    cuerpo += _boton(url_cursada if solo_creditos else url, "Ir a Inscripción a materias" if solo_creditos else "Ir al SIU Guaraní")
    titulo = "Inscripción a materias" if solo_creditos else "Novedades académicas"
    verbo = "Se detectó" if len(partes) == 1 and partes[0].startswith("1 ") else "Se detectaron"
    lista = partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]
    return _marco(titulo, f"{verbo} {lista}", cuerpo, "Novedades")


def html_aviso(titulo: str, mensaje: str, comando: str, url: str) -> str:
    cuerpo = f"""
<tr><td style="padding:14px 0 0 0;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="#fcf8e3"
         style="background:#fcf8e3;border:1px solid #fbeed5;border-radius:4px;">
    <tr><td style="padding:12px 14px;color:#c09853;font-family:{FONT};font-size:13px;line-height:1.5;">
      {escape(mensaje)}
      <div style="margin-top:8px;padding:6px 8px;background:#f5f5f5;border:1px solid #cccccc;border-radius:3px;color:{TEXTO};font-family:Consolas,'Courier New',monospace;font-size:12px;">{escape(comando)}</div>
    </td></tr>
  </table>
</td></tr>{_boton(url, "Ir al SIU Guaraní")}"""
    return _marco(titulo, titulo, cuerpo, "Aviso")
