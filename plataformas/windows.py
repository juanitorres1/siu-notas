"""Windows: Microsoft Edge + Programador de tareas (vía PowerShell)."""

import subprocess
from pathlib import Path

from plataformas import Plataforma

EDGE = [
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
]


def _ps(texto: str) -> str:
    """Literal de PowerShell entre comillas simples."""
    return "'" + texto.replace("'", "''") + "'"


def _powershell(script: str) -> str:
    resultado = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         "[Console]::OutputEncoding = [Text.Encoding]::UTF8\n" + script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if resultado.returncode != 0:
        raise RuntimeError(f"PowerShell falló:\n{resultado.stderr.strip()}")
    return resultado.stdout.strip()


class Windows(Plataforma):
    nombre = "Windows"
    # Edge viene con Windows. El Chromium que descarga Playwright no arrancaba con
    # ventana, y Edge además hace SSO con la cuenta de la Austral vinculada a Windows.
    canal_navegador = "msedge"

    @property
    def python(self) -> Path:
        return self.base / ".venv" / "Scripts" / "python.exe"

    @property
    def python_sin_consola(self) -> Path:
        return self.base / ".venv" / "Scripts" / "pythonw.exe"

    def verificar_requisitos(self) -> list[str]:
        problemas = []
        if not any(p.exists() for p in EDGE):
            problemas.append("No encontré Microsoft Edge instalado.")
        if not self.python_sin_consola.exists():
            problemas.append(f"No existe {self.python_sin_consola}: corré ./setup.sh primero.")
        return problemas

    def programar(self, nombre: str, intervalo_min: int) -> str:
        script = self.base / "siu_notas.py"
        return _powershell(f"""
            $accion  = New-ScheduledTaskAction -Execute {_ps(str(self.python_sin_consola))} `
                         -Argument {_ps(f'"{script}" check')} -WorkingDirectory {_ps(str(self.base))}
            $disparo = New-ScheduledTaskTrigger -Once -At (Get-Date) `
                         -RepetitionInterval (New-TimeSpan -Minutes {int(intervalo_min)})
            $config  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                         -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10) -MultipleInstances IgnoreNew
            Register-ScheduledTask -TaskName {_ps(nombre)} -Action $accion -Trigger $disparo -Settings $config -Force | Out-Null
            "Próxima ejecución: " + (Get-ScheduledTaskInfo -TaskName {_ps(nombre)}).NextRunTime
        """)

    def desprogramar(self, nombre: str) -> None:
        _powershell(f"Unregister-ScheduledTask -TaskName {_ps(nombre)} -Confirm:$false")
