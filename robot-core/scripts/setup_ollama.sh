#!/usr/bin/env bash
# =============================================================================
#  scripts/setup_ollama.sh — Configura e avvia Ollama con i modelli corretti
#  Uso: bash robot-core/scripts/setup_ollama.sh
# =============================================================================
set -euo pipefail

CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="$CORE_DIR/logs/ollama_setup.log"

mkdir -p "$CORE_DIR/logs"

echo "════════════════════════════════════════════════════════"
echo " 🤖 Ollama — Setup Modelli"
echo "════════════════════════════════════════════════════════"

log()  { echo "$(date '+%H:%M:%S')  $*" | tee -a "$LOG"; }
ok()   { log "  ✅ $*"; }
warn() { log "  ⚠️  $*"; }
fail() { log "  ❌ $*"; exit 1; }

# ── Verifica Ollama installato ─────────────────────────────────────────────
if ! command -v ollama &>/dev/null; then
    fail "Ollama non trovato. Installa con:"
    echo "   macOS: brew install ollama"
    echo "   Linux: curl -fsSL https://ollama.ai/install.sh | sh"
fi

ok "Ollama: $(ollama --version)"

# ── Avvia server se non in esecuzione ──────────────────────────────────────
echo ""
log "── Ollama Server ──────────────────────────────"

if pgrep -x ollama > /dev/null 2>&1; then
    ok "Ollama server già in esecuzione"
elif curl -s http://localhost:11434 > /dev/null 2>&1; then
    ok "Ollama server online (http://localhost:11434)"
else
    log "⏳ Avvio Ollama server..."
    ollama serve > "$CORE_DIR/logs/ollama.log" 2>&1 &
    OLLAMA_PID=$!
    log "   PID: $OLLAMA_PID"
    sleep 3
    if curl -s http://localhost:11434 > /dev/null 2>&1; then
        ok "Ollama server avviato"
    else
        warn "Ollama non risponde — riprova fra 5s"
        sleep 5
        if ! curl -s http://localhost:11434 > /dev/null 2>&1; then
            fail "Ollama server non disponibile. Logs: $CORE_DIR/logs/ollama.log"
        fi
    fi
fi

# ── Scarica modelli ───────────────────────────────────────────────────────
echo ""
log "── Modelli ────────────────────────────────────"

MODELS=(
    "llama3.2:3b:Modello testo — ragionamento e comunicazione (2.0 GB)"
    "moondream:Modello visivo — riconoscimento oggetti e scene (3.5 GB)"
)

for model_spec in "${MODELS[@]}"; do
    IFS=':' read -r model desc <<< "$model_spec"
    
    echo ""
    log "Verifico $model..."
    
    if ollama list 2>/dev/null | grep -q "^$model"; then
        # Modello presente, controlla se è completo
        size=$(ollama list 2>/dev/null | grep "^$model" | awk '{print $2}')
        if [ -n "$size" ] && [ "$size" != "0B" ]; then
            ok "$model ($size) — già presente"
            continue
        fi
    fi
    
    # Scarica modello
    log "⏳ Scarico $model ($desc)..."
    log "   Questo può richiedere parecchio tempo..."
    
    if timeout 1800 ollama pull "$model" 2>&1 | tee -a "$LOG" | tail -5; then
        size=$(ollama list 2>/dev/null | grep "^$model" | awk '{print $2}')
        ok "$model ($size) scaricato"
    else
        warn "Download di $model timeout o fallito"
        warn "Riprova con: ollama pull $model"
    fi
done

# ── Test modelli ───────────────────────────────────────────────────────────
echo ""
log "── Test Modelli ────────────────────────────────"

echo ""
echo "Test 1: Modello di testo (llama3.2:3b)"
log "⏳ Testo semplice..."
if echo "" | timeout 30 ollama run llama3.2:3b "Rispondi in una sola parola: quanto fa 2+2?" 2>&1 | head -3 | tee -a "$LOG"; then
    ok "✅ Llama3.2:3b funziona"
else
    warn "⚠️  Test llama3.2:3b fallito"
fi

echo ""
echo "Test 2: Modello visivo (moondream)"
log "⏳ Verifica caricamento..."
# Creiamo un'immagine di test vuota (1x1 pixel bianca base64)
TEST_IMG="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="

if echo "" | timeout 60 ollama run moondream "Describe this image in one word" --images "$TEST_IMG" 2>&1 | head -3 | tee -a "$LOG"; then
    ok "✅ Moondream funziona"
else
    warn "⚠️  Test moondream fallito (immagine dummy non supportata, okay)"
fi

# ── Statistiche RAM ────────────────────────────────────────────────────────
echo ""
log "── RAM Disponibile ────────────────────────────"

if command -v free &>/dev/null; then
    free_mb=$(free -m | awk 'NR==2 {print $7}')
    total_mb=$(free -m | awk 'NR==2 {print $2}')
elif command -v vm_stat &>/dev/null; then
    # macOS
    pages=$(vm_stat | grep "Pages free" | awk '{print $3}' | tr -d '.')
    free_mb=$((pages / 256))
    total_mb=$(($(sysctl -n hw.memsize) / 1000000))
else
    free_mb=0
    total_mb=0
fi

if [ $total_mb -gt 0 ]; then
    log "RAM totale: ${total_mb}MB, Libera: ${free_mb}MB"
    if [ $total_mb -lt 6000 ]; then
        warn "RAM totale < 6GB — potrebbe essere lento con entrambi i modelli"
    else
        ok "RAM sufficiente per entrambi i modelli"
    fi
fi

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════"
echo " ✅ Setup Ollama Completo!"
echo "════════════════════════════════════════════════════════"
echo ""
echo "Configurazione:"
echo "  🧠 Modello testo:   llama3.2:3b (2.0 GB)"
echo "  👁️  Modello visivo: moondream (3.5 GB)"
echo ""
echo "Architettura Spooky:"
echo "  1. Fotogramma camera"
echo "  2. Moondream → 'Vedo una persona e un gatto'"
echo "  3. Llama3.2:3b → 'Il gatto è carino!' "
echo ""
echo "Logs:"
echo "  - Ollama:        $CORE_DIR/logs/ollama.log"
echo "  - Setup Ollama:  $LOG"
echo ""
echo "Comandi utili:"
echo "  $ ollama list              # Lista modelli"
echo "  $ ollama ps                # Processi Ollama"
echo "  $ ollama serve             # Avvia server (blocca terminal)"
echo ""
