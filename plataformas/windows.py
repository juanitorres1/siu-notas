"""Windows: Microsoft Edge + Programador de tareas (vía PowerShell)."""

import os
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
        # Con SIU_NAVEGADOR (ej. chromium) no se usa Edge, así que no hace falta.
        if not os.getenv("SIU_NAVEGADOR") and not any(p.exists() for p in EDGE):
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

    def programar_al_iniciar(self, nombre: str, argumentos: list[str]) -> str:
        script = self.base / "siu_notas.py"
        linea = subprocess.list2cmdline([str(script), *argumentos])  # comillas correctas para alias con espacios
        # Sin ExecutionTimeLimit (0 = ilimitado): por defecto Windows mata las tareas a las 72 h.
        # Sin reintentos automáticos: un fallo ya avisa por mail y reintentar mandaría el aviso en bucle.
        return _powershell(f"""
            $accion  = New-ScheduledTaskAction -Execute {_ps(str(self.python_sin_consola))} `
                         -Argument {_ps(linea)} -WorkingDirectory {_ps(str(self.base))}
            $disparo = New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)
            $disparo.Delay = 'PT30S'   # deja que arranque la red
            $config  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                         -ExecutionTimeLimit (New-TimeSpan -Seconds 0) -MultipleInstances IgnoreNew
            Register-ScheduledTask -TaskName {_ps(nombre)} -Action $accion -Trigger $disparo -Settings $config -Force | Out-Null
            "Estado: " + (Get-ScheduledTask -TaskName {_ps(nombre)}).State
        """)

    def estado_tarea(self, nombre: str) -> str:
        return _powershell(f"""
            $t = Get-ScheduledTask -TaskName {_ps(nombre)} -ErrorAction SilentlyContinue
            if ($t) {{ $t.State.ToString() }} else {{ 'no existe' }}
        """)

    def iniciar_tarea(self, nombre: str) -> None:
        _powershell(f"Start-ScheduledTask -TaskName {_ps(nombre)}")

    def detener_tarea(self, nombre: str) -> None:
        _powershell(f"Stop-ScheduledTask -TaskName {_ps(nombre)} -ErrorAction SilentlyContinue; 'ok'")
