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

# Killa eventuale istanza precedente (tiene GPIO e porta)
PIDFILE="$CORE_DIR/spooky.pid"
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⏹  Fermo istanza precedente (PID $OLD_PID)..."
        kill "$OLD_PID" 2>/dev/null || true
        sleep 2
    fi
    rm -f "$PIDFILE"
fi
# Fallback: killa per nome nel caso il pid file sia perso
pkill -f "python.*main\.py" 2>/dev/null || true
sleep 1

echo "🕷️  Avvio Spooky..."
cd "$CORE_DIR"
python main.py "$@" &
echo $! > "$PIDFILE"
wait $!
