#!/usr/bin/env bash
# =============================================================================
#  install_mac.sh — Setup Spooky per macOS (Development)
#  Uso: bash robot-core/scripts/install_mac.sh
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORE_DIR="$REPO_DIR/robot-core"
VENV_DIR="$CORE_DIR/venv"
LOG="$CORE_DIR/logs/install_mac.log"

mkdir -p "$CORE_DIR"/{logs,data/faces,data/snapshots}

echo "════════════════════════════════════════════════════════"
echo " 🕷️  Spooky — Setup macOS"
echo " Repository: $REPO_DIR"
echo " Log: $LOG"
echo "════════════════════════════════════════════════════════"

log()  { echo "$(date '+%H:%M:%S')  $*" | tee -a "$LOG"; }
ok()   { log "  ✅ $*"; }
warn() { log "  ⚠️  $*"; }
fail() { log "  ❌ $*"; exit 1; }
step() { echo; log "── $* ────────────────────────────────"; }

# ── 1. Homebrew ───────────────────────────────────────────────────────────
step "Verifica Homebrew"
if command -v brew &>/dev/null; then
    ok "Homebrew: $(brew --version | head -1)"
else
    log "  ⏳ Installo Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
        || fail "Installazione Homebrew fallita"
    ok "Homebrew installato"
fi

# ── 2. Python 3.11+ ───────────────────────────────────────────────────────
step "Python 3.11+"
if command -v python3 &>/dev/null; then
    PYVER=$(python3 -c "import sys; print(sys.version_info >= (3,11))")
    if [ "$PYVER" = "True" ]; then
        ok "Python $(python3 --version)"
    else
        log "  ⏳ Installo Python 3.12 via Homebrew..."
        brew install python@3.12
        ok "Python 3.12 installato"
    fi
else
    log "  ⏳ Installo Python 3.12..."
    brew install python@3.12 || fail "Installazione Python fallita"
    ok "Python installato"
fi

# ── 3. Ollama ──────────────────────────────────────────────────────────────
step "Ollama (Modelli LLM/Vision)"
if command -v ollama &>/dev/null; then
    ok "Ollama: $(ollama --version)"
else
    log "  ⏳ Installo Ollama via Homebrew..."
    brew install ollama || fail "Installazione Ollama fallita"
    ok "Ollama installato"
fi

# ── 4. Pacchetti di sistema (optional ma consigliati) ──────────────────────
step "Pacchetti di sistema opzionali"
SYSTEM_PKGS=(
    opencv
    ffmpeg
)
for pkg in "${SYSTEM_PKGS[@]}"; do
    if brew list "$pkg" &>/dev/null 2>&1; then
        ok "$pkg (già installato)"
    else
        log "  ⏳ Installo $pkg..."
        brew install "$pkg" 2>&1 | tail -1
    fi
done

# ── 5. Virtual Environment ─────────────────────────────────────────────────
step "Virtual Environment Python"
if [ -f "$VENV_DIR/bin/activate" ]; then
    VENV_OK=$(python3 -c "import sys; sys.path.insert(0, '$VENV_DIR/lib/python3.11/site-packages'); print(sys.version_info >= (3,11))" 2>/dev/null || echo "False")
    if [ "$VENV_OK" != "True" ]; then
        warn "venv obsoleto — ricreo"
        rm -rf "$VENV_DIR"
    fi
fi

if [ ! -f "$VENV_DIR/bin/activate" ]; then
    log "  ⏳ Creo venv..."
    python3 -m venv "$VENV_DIR" 2>&1 | tail -1
    ok "venv creato"
fi

# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
ok "venv attivato: $(python --version)"

# ── 6. pip upgrade ────────────────────────────────────────────────────────
step "pip upgrade"
pip install --upgrade pip setuptools wheel --quiet
ok "pip $(pip --version | cut -d' ' -f2)"

# ── 7. Installa requirements ───────────────────────────────────────────────
step "Dipendenze Python (requirements.txt)"
log "  ⏳ pip install -r requirements.txt..."
if pip install -r "$CORE_DIR/requirements.txt" --quiet 2>&1 | tail -5 >> "$LOG"; then
    ok "Dipendenze installate"
else
    fail "Installazione dipendenze fallita"
fi

# ── 8. Configurazione Ollama ───────────────────────────────────────────────
step "Modelli Ollama"

# Controlla se Ollama è in esecuzione
if ! curl -s http://localhost:11434 > /dev/null 2>&1; then
    log "⏳ Avvio Ollama in background..."
    ollama serve > "$CORE_DIR/logs/ollama.log" 2>&1 &
    OLLAMA_PID=$!
    sleep 3
    if ! curl -s http://localhost:11434 > /dev/null 2>&1; then
        warn "Ollama non risponde — procedo comunque"
    else
        ok "Ollama avviato (PID $OLLAMA_PID)"
    fi
fi

# Scarica modelli
for model in "llama3.2:3b" "moondream"; do
    if ollama list 2>/dev/null | grep -q "^$model"; then
        ok "$model (già presente)"
    else
        log "  ⏳ ollama pull $model..."
        if timeout 600 ollama pull "$model" 2>&1 | tail -3 >> "$LOG"; then
            ok "$model scaricato"
        else
            warn "$model — download incompleto o timeout"
        fi
    fi
done

# ── 9. Configurazione locale ───────────────────────────────────────────────
step "Configurazione locale"
if [ ! -f "$CORE_DIR/config/local.yaml" ]; then
    if [ -f "$CORE_DIR/config/local.yaml.example" ]; then
        cp "$CORE_DIR/config/local.yaml.example" "$CORE_DIR/config/local.yaml"
        ok "local.yaml creato da template"
    else
        warn "local.yaml.example non trovato"
    fi
else
    ok "local.yaml già presente"
fi

# ── 10. Verifica Finale ────────────────────────────────────────────────────
step "Verifica Finale"

echo ""
echo "🔍 Checklist:"

# Test Ollama
if curl -s http://localhost:11434 > /dev/null 2>&1; then
    echo "  ✅ Ollama online (http://localhost:11434)"
else
    echo "  ⚠️  Ollama offline — esegui: ollama serve"
fi

# Test Modelli
for model in "llama3.2:3b" "moondream"; do
    if ollama list 2>/dev/null | grep -q "^$model"; then
        echo "  ✅ $model disponibile"
    else
        echo "  ❌ $model non trovato — scarica con: ollama pull $model"
    fi
done

# Test Python Imports
if python -c "import cv2, ollama, vosk, sounddevice, flask" 2>/dev/null; then
    echo "  ✅ Python packages OK"
else
    echo "  ❌ Alcuni packages non importabili"
fi

# Test Cartelle
[ -d "$CORE_DIR/data/faces" ] && echo "  ✅ data/faces/" || echo "  ❌ Crea: mkdir -p $CORE_DIR/data/faces"
[ -d "$CORE_DIR/logs" ] && echo "  ✅ logs/" || echo "  ❌ Crea: mkdir -p $CORE_DIR/logs"

echo ""
echo "════════════════════════════════════════════════════════"
echo " ✅ Setup completato!"
echo "════════════════════════════════════════════════════════"
echo ""
echo "Prossimi passi:"
echo "  1. Avvia Ollama in un Terminal:"
echo "     $ ollama serve"
echo ""
echo "  2. In un altro Terminal, avvia Spooky:"
echo "     $ cd $CORE_DIR"
echo "     $ source $VENV_DIR/bin/activate"
echo "     $ python main.py --debug"
echo ""
echo "📖 Documentazione: $REPO_DIR/SETUP.md"
echo ""
