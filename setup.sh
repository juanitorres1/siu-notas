#!/usr/bin/env bash
# Setup del monitor de notas del SIU.
#   ./setup.sh              -> crea el venv, instala dependencias y prepara .env
#   ./setup.sh programar    -> crea/actualiza la ejecución periódica (cada 60 min)
#   ./setup.sh desprogramar -> la elimina
#
# Este script no sabe nada del sistema operativo más allá de dónde queda el
# python del venv: los requisitos y la tarea programada los resuelve
# `siu_notas.py` a través de plataformas/ (hoy: Windows).
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

# venv: Scripts/ en Windows, bin/ en macOS/Linux.
venv_python() {
  if [ -x .venv/Scripts/python.exe ]; then echo .venv/Scripts/python.exe
  elif [ -x .venv/bin/python ]; then echo .venv/bin/python
  else return 1; fi
}

instalar() {
  if ! venv_python >/dev/null; then
    echo "==> Creando entorno virtual (.venv)"
    PY="$(command -v python || command -v python3)" || { echo "!! No encontré Python 3." >&2; exit 1; }
    "$PY" -m venv .venv
  fi
  local py; py="$(venv_python)"
  echo "==> Instalando dependencias"
  "$py" -m pip install -q --upgrade pip
  "$py" -m pip install -q -r requirements.txt

  if [ ! -f .env ]; then
    cp .env.example .env
    echo "==> Creé .env a partir de .env.example: completalo antes de seguir."
  else
    echo "==> .env ya existe, no lo toco."
  fi

  echo "==> Verificando requisitos del sistema"
  "$py" siu_notas.py verificar

  cat <<EOF

Listo. Próximos pasos:
  1. Completá .env (Gmail + SIU_CARRERA).
  2. Probá el mail:  $py siu_notas.py test-mail
  3. Probá el SIU:   $py siu_notas.py check
     (si dice "Sesión vencida": $py siu_notas.py login)
  4. Programalo:     ./setup.sh programar
EOF
}

case "${1:-instalar}" in
  instalar)               instalar ;;
  programar|desprogramar)
    py="$(venv_python)" || { echo "Primero corré ./setup.sh" >&2; exit 1; }
    "$py" siu_notas.py "$1" ;;
  *) echo "Uso: ./setup.sh [instalar|programar|desprogramar]" >&2; exit 1 ;;
esac
