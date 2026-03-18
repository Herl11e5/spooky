#!/usr/bin/env bash
# =============================================================================
#  install_rpi.sh — Setup completo Spooky su Raspberry Pi 5 (RPi OS Bookworm)
#  Uso: bash ~/spooky/robot-core/scripts/install_rpi.sh
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORE_DIR="$REPO_DIR/robot-core"
VENV_DIR="$CORE_DIR/venv"
LOG="$CORE_DIR/logs/install.log"

mkdir -p "$CORE_DIR/logs" "$CORE_DIR/data"

echo "════════════════════════════════════════════════════════"
echo " 🕷️  Spooky — Installazione su Raspberry Pi 5"
echo " Repository: $REPO_DIR"
echo " Log: $LOG"
echo "════════════════════════════════════════════════════════"

log()  { echo "$(date '+%H:%M:%S')  $*" | tee -a "$LOG"; }
ok()   { log "  ✅ $*"; }
warn() { log "  ⚠️  $*"; }
fail() { log "  ❌ $*"; exit 1; }
step() { echo; log "── $* ──────────────────────────────────────────"; }

# ── 1. Sistema operativo ──────────────────────────────────────────────────────
step "Verifica sistema"
if ! grep -q "Bookworm\|bookworm" /etc/os-release 2>/dev/null; then
    warn "Non sembra RPi OS Bookworm — proseguo comunque"
fi
ok "OS: $(. /etc/os-release && echo "$PRETTY_NAME")"

# ── 2. Python 3.11+ ──────────────────────────────────────────────────────────
step "Python 3.11+"
# Accetta 3.11, 3.12 o 3.13 — qualsiasi versione >= 3.11
_pick_python() {
    for v in python3.13 python3.12 python3.11; do
        command -v "$v" &>/dev/null && { echo "$v"; return; }
    done
    # Fallback: controlla se python3 di sistema è >= 3.11
    local ver
    ver=$(python3 -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null)
    [ "$ver" = "True" ] && { echo "python3"; return; }
    echo ""
}
PYTHON=$(_pick_python)
if [ -z "$PYTHON" ]; then
    warn "Python >= 3.11 non trovato — provo a installare python3.12..."
    sudo apt-get install -y python3.12 python3.12-venv 2>&1 | tee -a "$LOG" \
        || sudo apt-get install -y python3 python3-venv 2>&1 | tee -a "$LOG" \
        || fail "Impossibile installare Python"
    PYTHON=$(_pick_python)
    [ -z "$PYTHON" ] && fail "Python >= 3.11 non disponibile"
fi
ok "Python: $($PYTHON --version)"

# Assicura che python3-venv sia installato per la versione scelta
PYVER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
sudo apt-get install -y "python${PYVER}-venv" 2>&1 | tee -a "$LOG" || \
    sudo apt-get install -y python3-venv 2>&1 | tee -a "$LOG" || \
    warn "python${PYVER}-venv non disponibile — uso python3-venv"

# ── 3. Dipendenze di sistema ──────────────────────────────────────────────────
step "Pacchetti apt"
APT_PKGS=(
    git
    curl
    unzip
    python3-pip
    python3-setuptools
    python3-smbus           # I2C — richiesto da robot-hat
    espeak-ng
    alsa-utils
    pulseaudio-utils
    libatlas-base-dev       # numpy su ARM
    libopenblas-dev
    python3-picamera2       # picamera2 (solo da apt, non pip)
    python3-libcamera
    portaudio19-dev         # sounddevice
    libsndfile1
    libgpiod2               # GPIO su RPi 5
    python3-gpiod
)
sudo apt-get update -qq 2>&1 | tail -3 | tee -a "$LOG"
for pkg in "${APT_PKGS[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        log "  ✅ $pkg (già installato)"
    else
        log "  ⏳ apt install $pkg..."
        sudo apt-get install -y "$pkg" 2>&1 | tee -a "$LOG" \
            && ok "$pkg" || warn "$pkg — installazione fallita (non bloccante)"
    fi
done

# ── 4. Virtual environment ────────────────────────────────────────────────────
step "Virtual environment"
if [ -f "$VENV_DIR/bin/python" ]; then
    VENV_OK=$("$VENV_DIR/bin/python" -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null)
    if [ "$VENV_OK" != "True" ]; then
        warn "venv troppo vecchio ($("$VENV_DIR/bin/python" --version 2>&1)) — ricreo"
        rm -rf "$VENV_DIR"
    fi
fi
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    log "  ⏳ Creazione venv..."
    "$PYTHON" -m venv --system-site-packages "$VENV_DIR" 2>&1 | tee -a "$LOG"
fi
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
ok "venv attivato: $(python --version)"

# ── 5. pip upgrade ────────────────────────────────────────────────────────────
step "pip"
pip install --upgrade pip --quiet 2>&1 | tee -a "$LOG"
ok "pip $(pip --version | cut -d' ' -f2)"

# ── 6a. SunFounder robot-hat (metodo ufficiale) ───────────────────────────────
step "SunFounder robot-hat v2.0"
ROBOTHAT_DIR="$HOME/robot-hat"
if python3 -c "import robot_hat" &>/dev/null; then
    ok "robot-hat già installato"
else
    log "  ⏳ Clone robot-hat v2.0..."
    rm -rf "$ROBOTHAT_DIR"
    git clone -b v2.0 https://github.com/sunfounder/robot-hat.git --depth 1 "$ROBOTHAT_DIR" \
        2>&1 | tee -a "$LOG"
    log "  ⏳ Installazione robot-hat..."
    cd "$ROBOTHAT_DIR"
    sudo python3 install.py 2>&1 | tee -a "$LOG" \
        && ok "robot-hat installato" \
        || warn "robot-hat install.py fallito"
    cd -
fi

# ── 6b. SunFounder vilib (metodo ufficiale) ───────────────────────────────────
step "SunFounder vilib"
VILIB_DIR="$HOME/vilib"
if python3 -c "import vilib" &>/dev/null; then
    ok "vilib già installato"
else
    log "  ⏳ Clone vilib..."
    rm -rf "$VILIB_DIR"
    git clone https://github.com/sunfounder/vilib.git --depth 1 "$VILIB_DIR" \
        2>&1 | tee -a "$LOG"
    log "  ⏳ Installazione vilib..."
    cd "$VILIB_DIR"
    sudo python3 install.py 2>&1 | tee -a "$LOG" \
        && ok "vilib installato" \
        || warn "vilib install.py fallito (non bloccante)"
    cd -
fi

# ── 6c. SunFounder picrawler (metodo ufficiale) ───────────────────────────────
step "SunFounder picrawler"
PICRAWLER_DIR="$HOME/picrawler"
if python3 -c "from picrawler import Picrawler" &>/dev/null; then
    ok "picrawler già installato"
else
    log "  ⏳ Clone picrawler..."
    rm -rf "$PICRAWLER_DIR"
    git clone https://github.com/sunfounder/picrawler.git --depth 1 "$PICRAWLER_DIR" \
        2>&1 | tee -a "$LOG"
    log "  ⏳ Installazione picrawler (può richiedere qualche minuto)..."
    cd "$PICRAWLER_DIR"
    sudo python3 setup.py install 2>&1 | tee -a "$LOG" \
        && ok "picrawler installato" \
        || warn "picrawler setup.py fallito"
    cd -
fi

# ── 6d. I2S amplifier audio setup ─────────────────────────────────────────────
step "I2S amplifier (audio output)"
if [ -f "$ROBOTHAT_DIR/i2samp.sh" ]; then
    log "  ⏳ Configurazione I2S amplifier..."
    cd "$ROBOTHAT_DIR"
    # i2samp.sh chiede input interattivo — rispondo automaticamente con yes
    echo "y" | sudo bash i2samp.sh 2>&1 | tee -a "$LOG" \
        && ok "I2S amplifier configurato (richiede reboot per avere effetto)" \
        || warn "i2samp.sh fallito — audio output potrebbe non funzionare"
    cd -
else
    warn "i2samp.sh non trovato in $ROBOTHAT_DIR — esegui manualmente dopo"
fi

# ── 6e. Requirements Python (pacchetti Spooky) ───────────────────────────────
step "Requisiti Python (robot-core)"
pip install \
    pyyaml \
    numpy \
    flask \
    ollama \
    opencv-contrib-python \
    vosk \
    sounddevice \
    requests \
    2>&1 | tee -a "$LOG" || warn "Alcuni pacchetti pip hanno fallito"

# ── 7. Vosk model italiano ────────────────────────────────────────────────────
step "Modello Vosk italiano"
VOSK_DIR="$HOME/vosk-model-it"
if [ -d "$VOSK_DIR" ]; then
    ok "Vosk già presente: $VOSK_DIR"
else
    log "  📥 Download vosk-model-small-it-0.22 (~50 MB)..."
    VOSK_ZIP="/tmp/vosk-model-it.zip"
    curl -L --progress-bar \
        "https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip" \
        -o "$VOSK_ZIP" 2>&1 | tee -a "$LOG"
    unzip -q "$VOSK_ZIP" -d "$HOME" 2>&1 | tee -a "$LOG"
    extracted=$(ls -d "$HOME"/vosk-model-small-it* 2>/dev/null | head -1)
    if [ -n "$extracted" ]; then
        mv "$extracted" "$VOSK_DIR"
        ok "Vosk installato in $VOSK_DIR"
    else
        warn "Estrazione Vosk fallita — STT non disponibile"
    fi
    rm -f "$VOSK_ZIP"
fi

# ── 8. Ollama ─────────────────────────────────────────────────────────────────
step "Ollama"
if command -v ollama &>/dev/null; then
    ok "ollama già installato: $(ollama --version 2>&1 | head -1)"
else
    log "  📥 Installazione ollama..."
    curl -fsSL https://ollama.com/install.sh | sh 2>&1 | tee -a "$LOG" \
        || fail "Installazione ollama fallita"
    ok "ollama installato"
fi

# Avvia ollama serve in background se non attivo
if ! pgrep -x ollama &>/dev/null; then
    log "  ⏳ Avvio ollama serve..."
    ollama serve >> "$LOG" 2>&1 &
    for i in $(seq 1 20); do
        if curl -sf http://127.0.0.1:11434/api/tags &>/dev/null; then
            ok "ollama pronto (${i}s)"; break
        fi
        sleep 1
    done
else
    ok "ollama già in esecuzione"
fi

# Pull modello testo — llama3.2:3b per RPi 5 con 8 GB RAM
step "LLM model (llama3.2:3b)"
if ollama list 2>/dev/null | grep -q "llama3.2:3b"; then
    ok "llama3.2:3b già presente"
else
    log "  📥 Download llama3.2:3b (~2 GB, migliore qualità)..."
    ollama pull llama3.2:3b 2>&1 | tee -a "$LOG" \
        && ok "llama3.2:3b scaricato" \
        || warn "Download llama3.2:3b fallito — provo 1b come fallback..."
    # Fallback a 1b se 3b fallisce
    if ! ollama list 2>/dev/null | grep -q "llama3.2:3b"; then
        ollama pull llama3.2:1b 2>&1 | tee -a "$LOG" \
            && ok "llama3.2:1b scaricato (fallback)" \
            || warn "Download LLM fallito — scaricalo dopo con: ollama pull llama3.2:3b"
    fi
fi

# ── 9. Crea cartelle dati ─────────────────────────────────────────────────────
step "Struttura dati"
mkdir -p \
    "$CORE_DIR/data" \
    "$CORE_DIR/data/faces" \
    "$CORE_DIR/data/snapshots" \
    "$CORE_DIR/logs"
ok "Cartelle create"

# ── 10. Configura local.yaml se non esiste ────────────────────────────────────
step "Configurazione locale"
LOCAL_CFG="$CORE_DIR/config/local.yaml"
EXAMPLE_CFG="$CORE_DIR/config/local.yaml.example"
if [ ! -f "$LOCAL_CFG" ] && [ -f "$EXAMPLE_CFG" ]; then
    cp "$EXAMPLE_CFG" "$LOCAL_CFG"
    ok "Creato $LOCAL_CFG (modifica per personalizzare)"
elif [ -f "$LOCAL_CFG" ]; then
    ok "local.yaml già presente"
fi

# ── 11. Test audio ────────────────────────────────────────────────────────────
step "Test audio"
if command -v espeak-ng &>/dev/null && command -v aplay &>/dev/null; then
    TMP_WAV="/tmp/spooky_install_test.wav"
    if espeak-ng -v it -w "$TMP_WAV" "installazione completata" 2>/dev/null && \
       aplay -q "$TMP_WAV" 2>/dev/null; then
        ok "Audio funzionante"
    else
        warn "Test audio fallito — verifica altoparlante e /etc/asound.conf"
        aplay -l 2>&1 | grep "card" | tee -a "$LOG" || true
    fi
    rm -f "$TMP_WAV"
else
    warn "espeak-ng o aplay non disponibili"
fi

# ── 12. Systemd service ───────────────────────────────────────────────────────
step "Servizio systemd"
SERVICE_SRC="$CORE_DIR/scripts/spooky.service"
SERVICE_DST="/etc/systemd/system/spooky.service"

if [ -f "$SERVICE_SRC" ]; then
    # Sostituisci i placeholder con i percorsi reali
    sed \
        -e "s|__CORE_DIR__|$CORE_DIR|g" \
        -e "s|__VENV__|$VENV_DIR|g" \
        -e "s|__USER__|$(whoami)|g" \
        "$SERVICE_SRC" | sudo tee "$SERVICE_DST" > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl disable spooky.service 2>/dev/null || true
    ok "Servizio systemd installato (NON abilitato — avvio manuale)"
    log "  Avvio manuale:   bash $CORE_DIR/scripts/start.sh"
    log "  Abilita auto-avvio (opzionale): sudo systemctl enable spooky"
else
    warn "spooky.service non trovato in $SERVICE_SRC"
fi

# ── 13. Script di avvio rapido ────────────────────────────────────────────────
step "Script start.sh"
START_SCRIPT="$CORE_DIR/scripts/start.sh"
if [ -f "$START_SCRIPT" ]; then
    chmod +x "$START_SCRIPT"
    ok "start.sh pronto"
fi
chmod +x "$CORE_DIR/scripts/"*.sh 2>/dev/null || true

# ── Fine ──────────────────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════════════"
echo " ✅  Installazione completata!"
echo
echo "   ⚠️  REBOOT CONSIGLIATO per attivare I2S audio:"
echo "       sudo reboot"
echo
echo "   Dopo il reboot:"
echo "   Avvio manuale:  bash $CORE_DIR/scripts/start.sh"
echo "   Dashboard:      http://$(hostname -I | awk '{print $1}' 2>/dev/null || echo '<ip>'):5000"
echo "   Diagnosi hw:    python $CORE_DIR/scripts/diagnose.py all"
echo "   Registra volto: python $CORE_DIR/scripts/enroll_face.py --name \"Nome\" --id \"id\""
echo
echo "   Log installazione: $LOG"
echo "════════════════════════════════════════════════════════"
