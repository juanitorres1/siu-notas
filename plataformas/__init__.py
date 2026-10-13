"""Todo lo que depende del sistema operativo, detrás de una sola interfaz.

El resto del proyecto (scraping, comparación, mails) solo usa `Plataforma` y
nunca pregunta en qué sistema está corriendo.

Agregar un sistema nuevo (ej. macOS), sin tocar nada más:
  1. Crear `plataformas/macos.py` con una subclase de `Plataforma` que implemente
     los métodos abstractos (ver `plataformas/windows.py` como referencia).
  2. Registrarla en `REGISTRO` con la clave de `sys.platform` ("darwin").
"""

import importlib
import sys
from abc import ABC, abstractmethod
from pathlib import Path


class PlataformaNoSoportada(RuntimeError):
    pass


class Plataforma(ABC):
    """Contrato que cada sistema operativo tiene que cumplir."""

    #: Nombre para mostrar ("Windows", "macOS", ...).
    nombre: str = ""

    def __init__(self, base: Path):
        #: Carpeta del proyecto (donde está siu_notas.py y .venv).
        self.base = base

    # ---- navegador ----

    @property
    @abstractmethod
    def canal_navegador(self) -> str:
        """`channel` de Playwright para el navegador instalado en el sistema
        ("msedge", "chrome", ...). Se puede pisar con SIU_NAVEGADOR en el .env."""

    @abstractmethod
    def verificar_requisitos(self) -> list[str]:
        """Problemas que impiden correr (navegador faltante, etc.). Lista vacía = todo OK."""

    # ---- python del entorno virtual ----

    @property
    @abstractmethod
    def python(self) -> Path:
        """Intérprete del .venv (ej. .venv/Scripts/python.exe o .venv/bin/python)."""

    @property
    def python_sin_consola(self) -> Path:
        """Intérprete para la tarea programada, sin abrir ventanas.
        Por defecto el mismo; Windows usa pythonw.exe."""
        return self.python

    def comando(self, *args: str) -> str:
        """Comando para mostrar al usuario (mails, mensajes), relativo a la carpeta del proyecto."""
        return " ".join([self.python.relative_to(self.base).as_posix(), "siu_notas.py", *args])

    # ---- ejecución periódica ----

    @abstractmethod
    def programar(self, nombre: str, intervalo_min: int) -> str:
        """Crea o reemplaza la ejecución periódica de `siu_notas.py check`
        (Programador de tareas, launchd, cron...). Debe: correr con batería, recuperar
        corridas perdidas al despertar y no superponer corridas.
        Devuelve un texto con la próxima ejecución o el estado."""

    @abstractmethod
    def desprogramar(self, nombre: str) -> None:
        """Elimina la ejecución periódica creada por `programar`."""


# sys.platform -> (módulo, clase). Import perezoso: cada sistema solo carga lo suyo.
REGISTRO: dict[str, tuple[str, str]] = {
    "win32": ("plataformas.windows", "Windows"),
    # "darwin": ("plataformas.macos", "MacOS"),   # <- pendiente
    # "linux":  ("plataformas.linux", "Linux"),   # <- pendiente
}


def actual(base: Path) -> Plataforma:
    clave = sys.platform
    if clave not in REGISTRO:
        raise PlataformaNoSoportada(
            f"Sistema '{clave}' todavía no soportado (soportados: {', '.join(REGISTRO)}). "
            "Para agregarlo, implementá una subclase de Plataforma: ver plataformas/__init__.py."
        )
    modulo, clase = REGISTRO[clave]
    return getattr(importlib.import_module(modulo), clase)(base)
