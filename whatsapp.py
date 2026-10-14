"""Aviso corto por WhatsApp vía CallMeBot (https://www.callmebot.com/blog/free-api-whatsapp-messages/).

Canal complementario al mail: si no está configurado no hace nada, y si falla
nunca rompe la corrida (el mail sigue siendo el aviso principal).

- Los mensajes salen del número de CallMeBot hacia el tuyo: tu cuenta de WhatsApp
  no se automatiza. La API gratuita es solo para uso personal.
- El texto pasa por el servidor de CallMeBot. Por eso, por defecto se manda solo un
  resumen sin notas ni materias (WHATSAPP_DETALLE=completo para incluir el detalle).

Configuración (.env):
  WHATSAPP_TELEFONO=+549XXXXXXXXXX   # con código de país; en Argentina +549 + área + número, sin 15
  WHATSAPP_APIKEY=1234567           # la que te manda CallMeBot al activarlo
  WHATSAPP_DETALLE=resumen          # resumen (por defecto) | completo
"""

import os
import re
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.callmebot.com/whatsapp.php"
MAX_CARACTERES = 1500  # margen para que la URL no sea gigante


def configurado() -> bool:
    return bool(os.getenv("WHATSAPP_TELEFONO") and os.getenv("WHATSAPP_APIKEY"))


def detalle_completo() -> bool:
    return os.getenv("WHATSAPP_DETALLE", "resumen").strip().lower() == "completo"


def enviar(texto: str) -> str:
    """Manda `texto` (admite *negrita* de WhatsApp). Devuelve la respuesta de CallMeBot.
    Lanza RuntimeError si no está configurado o si CallMeBot reporta un error."""
    if not configurado():
        raise RuntimeError("Falta WHATSAPP_TELEFONO o WHATSAPP_APIKEY en .env")
    if len(texto) > MAX_CARACTERES:
        texto = texto[: MAX_CARACTERES - 1] + "…"
    params = urllib.parse.urlencode({
        "phone": os.environ["WHATSAPP_TELEFONO"].strip(),
        "text": texto,
        "apikey": os.environ["WHATSAPP_APIKEY"].strip(),
    })
    pedido = urllib.request.Request(f"{API}?{params}", headers={"User-Agent": "siu-notas"})
    try:
        with urllib.request.urlopen(pedido, timeout=30) as r:
            respuesta = r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"CallMeBot respondió HTTP {e.code}") from None
    # CallMeBot contesta 200 con un HTML tanto si sale como si no; el resultado viene en el
    # texto ("Message queued..." vs. "APIKey is invalid", etc.).
    plano = " ".join(re.sub(r"<[^>]+>", " ", respuesta).split())
    if not re.search(r"\b(queued|sent)\b", plano, re.I):
        # No incluimos la URL en el error: lleva la apikey.
        raise RuntimeError(f"CallMeBot no confirmó el envío: {plano[:200]}")
    return plano[:200]
