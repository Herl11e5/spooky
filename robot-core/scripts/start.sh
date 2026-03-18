#!/usr/bin/env bash
# start.sh — Avvio manuale Spooky (senza systemd)
set -euo pipefail

CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$CORE_DIR/venv"

# Assicurati che ollama sia in esecuzione
if command -v ollama &>/dev/null && ! pgrep -x ollama &>/dev/null; then
    echo "⏳ Avvio ollama serve..."
    ollama serve >> "$CORE_DIR/logs/ollama.log" 2>&1 &
    sleep 3
fi

# Attiva venv se presente
if [ -f "$VENV/bin/activate" ]; then
    # shellcheck disable=SC1090
    source "$VENV/bin/activate"
    echo "✅ venv: $(python --version)"
else
    echo "⚠️  venv non trovato in $VENV — esegui install_rpi.sh prima"
fi

mkdir -p "$CORE_DIR/logs" "$CORE_DIR/data"

echo "🕷️  Avvio Spooky..."
cd "$CORE_DIR"
exec python main.py "$@"
