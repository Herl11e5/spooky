#!/usr/bin/env bash
# =============================================================================
#  install_rpi.sh — Setup completo Spooky su Raspberry Pi 5 (RPi OS Bookworm)
#  Uso: bash ~/spooky/robot-core/scripts/install_rpi.sh
# =============================================================================
set -eo pipefail   # errexit + pipefail; NO -u (array vuoti sicuri in bash<5)

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORE_DIR="$REPO_DIR/robot-core"
VENV_DIR="$CORE_DIR/venv"
LOG="$CORE_DIR/logs/install.log"

mkdir -p "$CORE_DIR/logs" "$CORE_DIR/data" "$CORE_DIR/data/faces" "$CORE_DIR/data/snapshots"

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

FAILED_PKGS=()
WARNINGS=()

# ── 0. Aggiorna repository ────────────────────────────────────────────────────
step "Aggiornamento repository"
if git -C "$REPO_DIR" rev-parse --is-inside-work-tree &>/dev/null; then
    if git -C "$REPO_DIR" diff --quiet && git -C "$REPO_DIR" diff --cached --quiet; then
        git -C "$REPO_DIR" pull --ff-only 2>&1 | tee -a "$LOG" \
            && ok "Repository aggiornato" \
            || warn "git pull fallito — uso versione locale"
    else
        warn "Modifiche locali presenti — salto git pull"
    fi
fi

# ── 1. Sistema operativo ──────────────────────────────────────────────────────
step "Verifica sistema"
OS_ID=$(. /etc/os-release && echo "$ID" 2>/dev/null || echo "unknown")
OS_VERSION=$(. /etc/os-release && echo "$VERSION_ID" 2>/dev/null || echo "unknown")
OS_PRETTY=$(. /etc/os-release && echo "$PRETTY_NAME")

# Rileva Debian/RPi OS version
IS_BOOKWORM=false
IS_TRIXIE=false
[ "$OS_VERSION" = "12" ] || grep -q "Bookworm" /etc/os-release 2>/dev/null && IS_BOOKWORM=true
[ "$OS_VERSION" = "13" ] || grep -q "trixie" /etc/os-release 2>/dev/null && IS_TRIXIE=true

if [ "$IS_TRIXIE" = true ]; then
    warn "Debian trixie (testing) rilevato — alcuni pacchetti apt potrebbero non essere disponibili"
elif [ "$IS_BOOKWORM" != true ]; then
    warn "Non sembra RPi OS Bookworm — proseguo comunque"
fi
ok "OS: $OS_PRETTY"
ok "Arch: $(uname -m)"

# ── 2. Python 3.11+ ──────────────────────────────────────────────────────────
step "Python 3.11+"
_pick_python() {
    for v in python3.13 python3.12 python3.11; do
        command -v "$v" &>/dev/null && { echo "$v"; return; }
    done
    local ver
    ver=$(python3 -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null || echo "False")
    [ "$ver" = "True" ] && { echo "python3"; return; }
    echo ""
}
PYTHON=$(_pick_python)
if [ -z "$PYTHON" ]; then
    warn "Python >= 3.11 non trovato — provo a installare python3.12..."
    sudo apt-get install -y python3.12 python3.12-venv python3.12-full 2>&1 | tee -a "$LOG" \
        || sudo apt-get install -y python3 python3-venv 2>&1 | tee -a "$LOG" \
        || fail "Impossibile installare Python"
    PYTHON=$(_pick_python)
    [ -z "$PYTHON" ] && fail "Python >= 3.11 non disponibile"
fi
ok "Python: $($PYTHON --version)"

PYVER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
sudo apt-get install -y "python${PYVER}-venv" "python${PYVER}-full" 2>&1 | tee -a "$LOG" \
    || sudo apt-get install -y python3-venv 2>&1 | tee -a "$LOG" \
    || warn "python${PYVER}-venv non disponibile"

# ── 3. Pacchetti apt ──────────────────────────────────────────────────────────
step "Pacchetti apt"

# Base packages (sempre disponibili)
APT_PKGS=(
    git curl unzip wget
    python3-pip python3-setuptools python3-wheel
    python3-smbus
    espeak-ng
    alsa-utils pulseaudio-utils
    libopenblas-dev
    libopenjp2-7 libwebp-dev
    ffmpeg
    python3-picamera2 python3-libcamera
    portaudio19-dev libsndfile1
    i2c-tools
    libi2c-dev
)

# Optional packages (version-specific)
OPT_PKGS=()
if [ "$IS_BOOKWORM" = true ]; then
    # Bookworm ha questi pacchetti
    OPT_PKGS+=(python3-gpiod libgpiod2)
elif [ "$IS_TRIXIE" = true ]; then
    # trixie non ha python3-gpiod / libgpiod2 (obsoleti)
    warn "su Debian trixie: python3-gpiod e libgpiod2 non disponibili (robot-hat ha GPIO proprio)"
else
    # tenta comunque
    OPT_PKGS+=(python3-gpiod libgpiod2)
fi

# libatlas-base-dev è obsoleto → use libopenblas-dev (già in lista)

APT_ALL=("${APT_PKGS[@]}" "${OPT_PKGS[@]}")

sudo apt-get update -qq 2>&1 | tail -3 | tee -a "$LOG"
for pkg in "${APT_ALL[@]}"; do
    if dpkg -s "$pkg" &>/dev/null; then
        log "  ✅ $pkg (già installato)"
    else
        log "  ⏳ apt install $pkg..."
        if sudo apt-get install -y "$pkg" 2>&1 | tee -a "$LOG"; then
            ok "$pkg"
        else
            warn "$pkg — installazione fallita (non bloccante)"
            WARNINGS+=("apt: $pkg non installato")
        fi
    fi
done

# ── 3b. Abilita I2C e SPI (richiesto per MPU6050 + robot_hat) ────────────────
step "Abilitazione I2C / SPI"
if command -v raspi-config &>/dev/null; then
    sudo raspi-config nonint do_i2c 0 2>/dev/null && ok "I2C abilitato" || warn "Abilitazione I2C fallita"
    sudo raspi-config nonint do_spi 0 2>/dev/null && ok "SPI abilitato" || warn "Abilitazione SPI fallita"
else
    # Fallback diretto su /boot/firmware/config.txt
    CONFIG_TXT="/boot/firmware/config.txt"
    [ -f /boot/config.txt ] && CONFIG_TXT="/boot/config.txt"
    if [ -f "$CONFIG_TXT" ]; then
        grep -q "^dtparam=i2c_arm=on" "$CONFIG_TXT" \
            || { echo "dtparam=i2c_arm=on" | sudo tee -a "$CONFIG_TXT" > /dev/null; ok "I2C abilitato in $CONFIG_TXT"; }
        grep -q "^dtparam=spi=on" "$CONFIG_TXT" \
            || { echo "dtparam=spi=on"     | sudo tee -a "$CONFIG_TXT" > /dev/null; ok "SPI abilitato in $CONFIG_TXT"; }
    else
        warn "config.txt non trovato — abilita I2C manualmente con raspi-config"
        WARNINGS+=("I2C: abilitazione manuale richiesta (raspi-config → Interface Options → I2C)")
    fi
fi
# Aggiungi utente al gruppo i2c
sudo usermod -aG i2c "$(whoami)" 2>/dev/null || true

# ── 4. Virtual environment ────────────────────────────────────────────────────
step "Virtual environment"
if [ -f "$VENV_DIR/bin/python" ]; then
    VENV_OK=$("$VENV_DIR/bin/python" -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null || echo "False")
    if [ "$VENV_OK" != "True" ]; then
        warn "venv obsoleto ($("$VENV_DIR/bin/python" --version 2>&1)) — ricreo"
        rm -rf "$VENV_DIR"
    fi
fi
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    log "  ⏳ Creazione venv con --system-site-packages..."
    "$PYTHON" -m venv --system-site-packages "$VENV_DIR" 2>&1 | tee -a "$LOG" \
        || fail "Creazione venv fallita"
fi
# shellcheck disable=SC1090
source "$VENV_DIR/bin/activate"
ok "venv attivato: $(python --version)"
PIP="$VENV_DIR/bin/pip"   # pip esplicito del venv

# ── 5. pip upgrade ────────────────────────────────────────────────────────────
step "pip"
"$PIP" install --upgrade pip setuptools wheel --quiet 2>&1 | tee -a "$LOG"
ok "pip $("$PIP" --version | cut -d' ' -f2)"

# ── 6a. SunFounder robot-hat ──────────────────────────────────────────────────
step "SunFounder robot-hat v2.0"
ROBOTHAT_DIR="$HOME/robot-hat"
if python -c "import robot_hat" &>/dev/null 2>&1; then
    ok "robot-hat già importabile dal venv"
else
    log "  ⏳ Clone robot-hat v2.0..."
    rm -rf "$ROBOTHAT_DIR"
    if git clone -b v2.0 https://github.com/sunfounder/robot-hat.git --depth 1 "$ROBOTHAT_DIR" \
            2>&1 | tee -a "$LOG"; then
        if [ -f "$ROBOTHAT_DIR/install.py" ]; then
            log "  ⏳ Installazione robot-hat (sistema + venv)..."
            pushd "$ROBOTHAT_DIR" > /dev/null
            # install.py fa setup sistema (GPIO, gruppi, config.txt)
            sudo python3 install.py 2>&1 | tee -a "$LOG" || true
            # installa il package Python nel nostro venv
            "$PIP" install . --quiet 2>&1 | tee -a "$LOG" \
                && ok "robot-hat installato nel venv" \
                || { warn "pip install robot-hat fallito"; WARNINGS+=("robot-hat: pip install fallito"); }
            popd > /dev/null
        fi
    else
        warn "Clone robot-hat fallito"
        WARNINGS+=("robot-hat: clone fallito")
    fi
fi

# ── 6b. SunFounder vilib ──────────────────────────────────────────────────────
step "SunFounder vilib"
VILIB_DIR="$HOME/vilib"
if python -c "import vilib" &>/dev/null 2>&1; then
    ok "vilib già importabile dal venv"
else
    log "  ⏳ Clone vilib..."
    rm -rf "$VILIB_DIR"
    if git clone https://github.com/sunfounder/vilib.git --depth 1 "$VILIB_DIR" \
            2>&1 | tee -a "$LOG"; then
        if [ -f "$VILIB_DIR/install.py" ]; then
            pushd "$VILIB_DIR" > /dev/null
            sudo python3 install.py 2>&1 | tee -a "$LOG" || true
            "$PIP" install . --quiet 2>&1 | tee -a "$LOG" \
                && ok "vilib installato nel venv" \
                || { warn "pip install vilib fallito (non bloccante)"; WARNINGS+=("vilib: pip install fallito"); }
            popd > /dev/null
        fi
    else
        warn "Clone vilib fallito"
        WARNINGS+=("vilib: clone fallito")
    fi
fi

# ── 6c. SunFounder picrawler ──────────────────────────────────────────────────
step "SunFounder picrawler"
PICRAWLER_DIR="$HOME/picrawler"
if python -c "from picrawler import Picrawler" &>/dev/null 2>&1; then
    ok "picrawler già importabile dal venv"
else
    log "  ⏳ Clone picrawler..."
    rm -rf "$PICRAWLER_DIR"
    if git clone https://github.com/sunfounder/picrawler.git --depth 1 "$PICRAWLER_DIR" \
            2>&1 | tee -a "$LOG"; then
        pushd "$PICRAWLER_DIR" > /dev/null
        if [ -f "setup.cfg" ] || [ -f "pyproject.toml" ]; then
            "$PIP" install . --quiet 2>&1 | tee -a "$LOG" \
                && ok "picrawler installato (pip)" \
                || { warn "pip install picrawler fallito"; WARNINGS+=("picrawler: pip install fallito"); }
        elif [ -f "setup.py" ]; then
            "$PIP" install . --quiet 2>&1 | tee -a "$LOG" \
                || sudo python3 setup.py install 2>&1 | tee -a "$LOG" \
                && ok "picrawler installato" \
                || { warn "picrawler install fallito"; WARNINGS+=("picrawler: install fallito"); }
        else
            warn "picrawler: nessun file di installazione trovato"
            WARNINGS+=("picrawler: setup.py/pyproject.toml non trovati")
        fi
        popd > /dev/null
    else
        warn "Clone picrawler fallito"
        WARNINGS+=("picrawler: clone fallito")
    fi
fi

# ── 6d. I2S amplifier ────────────────────────────────────────────────────────
step "I2S amplifier (audio output)"
if [ -f "$ROBOTHAT_DIR/i2samp.sh" ]; then
    log "  ⏳ Configurazione I2S amplifier..."
    pushd "$ROBOTHAT_DIR" > /dev/null
    echo "y" | sudo bash i2samp.sh 2>&1 | tee -a "$LOG" \
        && ok "I2S amplifier configurato (attivo dopo reboot)" \
        || { warn "i2samp.sh fallito — audio output potrebbe non funzionare"; WARNINGS+=("I2S: configurazione fallita"); }
    popd > /dev/null
else
    warn "i2samp.sh non trovato in $ROBOTHAT_DIR — robot-hat non clonato correttamente?"
    WARNINGS+=("I2S: i2samp.sh non trovato")
fi

# ── 6e. Pacchetti Python nel venv ─────────────────────────────────────────────
step "Pacchetti Python"

_pip_install() {
    local pkg="$1"
    local desc="${2:-$1}"
    log "  ⏳ pip install $pkg..."
    if "$PIP" install "$pkg" --quiet 2>&1 | tee -a "$LOG"; then
        ok "$desc"
    else
        warn "$desc — installazione fallita"
        FAILED_PKGS+=("$pkg")
    fi
}

_pip_install "pyyaml"           "PyYAML"
_pip_install "numpy"            "NumPy"
_pip_install "flask"            "Flask"
_pip_install "ollama"           "ollama client"
_pip_install "vosk"             "Vosk STT"
_pip_install "sounddevice"      "sounddevice"
_pip_install "requests"         "requests"
_pip_install "psutil"           "psutil"
_pip_install "smbus2"           "smbus2 (I2C / MPU6050 edge detection)"

# opencv: prova prima la versione completa, poi headless
log "  ⏳ pip install opencv-contrib-python..."
if "$PIP" install opencv-contrib-python --quiet 2>&1 | tee -a "$LOG"; then
    ok "OpenCV (full)"
elif "$PIP" install opencv-contrib-python-headless --quiet 2>&1 | tee -a "$LOG"; then
    ok "OpenCV (headless fallback)"
else
    warn "OpenCV pip fallito — provo pacchetto di sistema..."
    sudo apt-get install -y python3-opencv 2>&1 | tee -a "$LOG" \
        && ok "OpenCV (apt fallback)" \
        || { warn "OpenCV — tutti i metodi falliti"; FAILED_PKGS+=("opencv"); }
fi

# ── 7. Vosk model italiano ────────────────────────────────────────────────────
step "Modello Vosk italiano"
VOSK_DIR="$HOME/vosk-model-it"
if [ -d "$VOSK_DIR" ] && [ -f "$VOSK_DIR/conf/model.conf" ]; then
    ok "Vosk già presente: $VOSK_DIR"
else
    log "  📥 Download vosk-model-small-it-0.22 (~50 MB)..."
    VOSK_ZIP="/tmp/vosk-model-it.zip"
    if curl -L --progress-bar \
        "https://alphacephei.com/vosk/models/vosk-model-small-it-0.22.zip" \
        -o "$VOSK_ZIP" 2>&1 | tee -a "$LOG"; then
        unzip -q "$VOSK_ZIP" -d "$HOME" 2>&1 | tee -a "$LOG"
        extracted=$(ls -d "$HOME"/vosk-model-small-it* 2>/dev/null | head -1)
        if [ -n "$extracted" ]; then
            rm -rf "$VOSK_DIR"
            mv "$extracted" "$VOSK_DIR"
            ok "Vosk installato: $VOSK_DIR"
        else
            warn "Estrazione Vosk fallita"
            WARNINGS+=("Vosk: estrazione fallita — STT non disponibile")
        fi
        rm -f "$VOSK_ZIP"
    else
        warn "Download Vosk fallito — STT non disponibile"
        WARNINGS+=("Vosk: download fallito")
    fi
fi

# ── 8. Piper TTS (voce neurale italiana — stile Baymax) ──────────────────────
step "Piper TTS (voce neurale italiana)"
PIPER_VOICES_DIR="$HOME/piper-voices"
PIPER_VOICE="it_IT-riccardo-x_low"
PIPER_ONNX="$PIPER_VOICES_DIR/${PIPER_VOICE}.onnx"
PIPER_JSON="$PIPER_VOICES_DIR/${PIPER_VOICE}.onnx.json"

# Installa piper-tts nel venv
log "  ⏳ pip install piper-tts..."
if "$PIP" install piper-tts --quiet 2>&1 | tee -a "$LOG"; then
    ok "piper-tts installato"
else
    warn "piper-tts pip fallito — voce neurale non disponibile (fallback: espeak-ng)"
    WARNINGS+=("piper-tts: installazione fallita")
fi

# Scarica modello vocale
mkdir -p "$PIPER_VOICES_DIR"
if [ -f "$PIPER_ONNX" ] && [ -f "$PIPER_JSON" ]; then
    ok "Modello piper già presente: $PIPER_VOICE"
else
    HF_BASE="https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/it/it_IT/riccardo/x_low"
    log "  📥 Download modello piper ($PIPER_VOICE)..."
    DL_OK=true
    curl -L --progress-bar "$HF_BASE/${PIPER_VOICE}.onnx"      -o "$PIPER_ONNX" 2>&1 | tee -a "$LOG" || DL_OK=false
    curl -L --progress-bar "$HF_BASE/${PIPER_VOICE}.onnx.json" -o "$PIPER_JSON" 2>&1 | tee -a "$LOG" || DL_OK=false
    if [ "$DL_OK" = true ] && [ -f "$PIPER_ONNX" ] && [ -f "$PIPER_JSON" ]; then
        ok "Modello piper scaricato: $PIPER_VOICE"
    else
        warn "Download modello piper fallito — Spooky userà espeak-ng"
        WARNINGS+=("piper: download modello fallito — fallback espeak-ng")
        rm -f "$PIPER_ONNX" "$PIPER_JSON"
    fi
fi

# ── 9. Ollama ─────────────────────────────────────────────────────────────────
step "Ollama"
if command -v ollama &>/dev/null; then
    ok "ollama già installato: $(ollama --version 2>&1 | head -1)"
else
    log "  📥 Installazione ollama..."
    curl -fsSL https://ollama.com/install.sh | sh 2>&1 | tee -a "$LOG" \
        || fail "Installazione ollama fallita"
    ok "ollama installato"
fi

# Avvia ollama serve se non attivo
if ! pgrep -x ollama &>/dev/null; then
    log "  ⏳ Avvio ollama serve..."
    nohup ollama serve >> "$LOG" 2>&1 &
    log "  ⏳ Attendo ollama (max 40s)..."
    READY=0
    for i in $(seq 1 40); do
        if curl -sf http://127.0.0.1:11434/api/tags &>/dev/null; then
            ok "ollama pronto (${i}s)"; READY=1; break
        fi
        sleep 1
    done
    [ "$READY" -eq 0 ] && warn "ollama non risponde dopo 40s — i pull potrebbero fallire"
else
    ok "ollama già in esecuzione"
fi

# Pull LLM testo
step "LLM model (llama3.2:3b)"
if ollama list 2>/dev/null | grep -q "llama3.2:3b"; then
    ok "llama3.2:3b già presente"
else
    log "  📥 Download llama3.2:3b (~2 GB)..."
    ollama pull llama3.2:3b 2>&1 | tee -a "$LOG" || true
    if ollama list 2>/dev/null | grep -q "llama3.2"; then
        ok "llama3.2:3b scaricato"
    else
        warn "llama3.2:3b fallito — provo fallback llama3.2:1b..."
        ollama pull llama3.2:1b 2>&1 | tee -a "$LOG" || true
        if ollama list 2>/dev/null | grep -q "llama3.2"; then
            ok "llama3.2:1b scaricato (fallback)"
        else
            warn "Download LLM fallito — esegui: ollama pull llama3.2:3b"
            WARNINGS+=("LLM: nessun modello testo disponibile")
        fi
    fi
fi

# Pull modello visione
step "Vision model (moondream)"
if ollama list 2>/dev/null | grep -q "moondream"; then
    ok "moondream già presente"
else
    log "  📥 Download moondream (~1.6 GB)..."
    ollama pull moondream 2>&1 | tee -a "$LOG" || true
    if ollama list 2>/dev/null | grep -q "moondream"; then
        ok "moondream scaricato"
    else
        warn "Download moondream fallito — esegui: ollama pull moondream"
        WARNINGS+=("Vision: moondream non scaricato")
    fi
fi

# ── 10. Configurazione locale ─────────────────────────────────────────────────
step "Configurazione locale"
LOCAL_CFG="$CORE_DIR/config/local.yaml"
EXAMPLE_CFG="$CORE_DIR/config/local.yaml.example"
if [ ! -f "$LOCAL_CFG" ] && [ -f "$EXAMPLE_CFG" ]; then
    cp "$EXAMPLE_CFG" "$LOCAL_CFG"
    ok "Creato $LOCAL_CFG"
elif [ -f "$LOCAL_CFG" ]; then
    ok "local.yaml già presente"
else
    warn "local.yaml.example non trovato — crea $LOCAL_CFG manualmente"
fi

# ── 11. Test audio ────────────────────────────────────────────────────────────
step "Test audio"
if command -v espeak-ng &>/dev/null && command -v aplay &>/dev/null; then
    TMP_WAV="/tmp/spooky_test.wav"
    espeak-ng -v it -w "$TMP_WAV" "ciao, installazione completata" 2>/dev/null || true

    log "  Dispositivi audio rilevati:"
    aplay -l 2>&1 | grep "^card" | while IFS= read -r l; do log "    $l"; done || true

    # Costruisci lista: solo dispositivi non-HDMI tramite nome CARD stabile
    AUDIO_DEVS=()
    while IFS= read -r line; do
        low="${line,,}"
        if [[ "$line" == card* ]] && [[ "$low" != *hdmi* ]] && [[ "$low" != *vc4* ]]; then
            card_name=$(echo "$line" | sed -n 's/^card [0-9]*: \([^ ]*\).*/\1/p')
            if [ -n "$card_name" ]; then
                AUDIO_DEVS+=("plughw:CARD=${card_name},DEV=0")
            fi
        fi
    done < <(aplay -l 2>/dev/null)

    if [ ${#AUDIO_DEVS[@]} -eq 0 ]; then
        warn "Nessuna scheda audio non-HDMI trovata — REBOOT NECESSARIO dopo I2S setup"
        WARNINGS+=("Audio: nessuna scheda non-HDMI — esegui reboot poi ritesta con: aplay -l")
    else
        PLAYED=0
        for dev in "${AUDIO_DEVS[@]}"; do
            if [ -f "$TMP_WAV" ] && aplay -q -D "$dev" "$TMP_WAV" 2>/dev/null; then
                ok "Audio OK — device: $dev"
                PLAYED=1; break
            fi
        done
        [ "$PLAYED" -eq 0 ] && {
            warn "Test audio fallito su tutti i dispositivi non-HDMI"
            WARNINGS+=("Audio: test fallito — riprova dopo reboot")
        }
    fi
    rm -f "$TMP_WAV"
else
    warn "espeak-ng o aplay non disponibili"
    WARNINGS+=("Audio: espeak-ng o aplay mancanti")
fi

# ── 12. Systemd service (avvio manuale) ──────────────────────────────────────
step "Servizio systemd"
SERVICE_SRC="$CORE_DIR/scripts/spooky.service"
SERVICE_DST="/etc/systemd/system/spooky.service"
if [ -f "$SERVICE_SRC" ]; then
    sed \
        -e "s|__CORE_DIR__|$CORE_DIR|g" \
        -e "s|__VENV__|$VENV_DIR|g" \
        -e "s|__USER__|$(whoami)|g" \
        "$SERVICE_SRC" | sudo tee "$SERVICE_DST" > /dev/null
    sudo systemctl daemon-reload
    sudo systemctl disable spooky.service 2>/dev/null || true
    ok "Servizio installato (NON auto-avviato — usa start.sh)"
else
    warn "spooky.service non trovato"
fi

# ── 13. Script avvio ─────────────────────────────────────────────────────────
step "Script start.sh"
chmod +x "$CORE_DIR/scripts/"*.sh 2>/dev/null || true
if [ -f "$CORE_DIR/scripts/start.sh" ]; then
    ok "start.sh pronto"
fi

# ── 14. Verifica importazioni Python ─────────────────────────────────────────
step "Verifica importazioni Python"
VERIFY_MODS=(numpy cv2 flask vosk sounddevice ollama psutil yaml smbus2 piper)
for mod in "${VERIFY_MODS[@]}"; do
    if python -c "import $mod" &>/dev/null 2>&1; then
        ok "import $mod"
    else
        warn "import $mod FALLITO"
        FAILED_PKGS+=("$mod")
    fi
done

# SunFounder libs — non bloccanti (possono mancare su macchine non-RPi)
for mod in robot_hat picrawler picamera2; do
    if python -c "import $mod" &>/dev/null 2>&1; then
        ok "import $mod (hardware)"
    else
        warn "import $mod non disponibile (normale se non su RPi)"
    fi
done

# ── Riepilogo finale ──────────────────────────────────────────────────────────
echo
echo "════════════════════════════════════════════════════════"

if [ ${#FAILED_PKGS[@]} -eq 0 ] && [ ${#WARNINGS[@]} -eq 0 ]; then
    echo " ✅  Installazione completata senza errori!"
else
    echo " ⚠️   Installazione completata con avvisi:"
    if [ "$IS_TRIXIE" = true ]; then
        echo "     • Debian trixie (testing) — alcuni pacchetti apt non disponibili (ATTESO)"
    fi
    for w in "${WARNINGS[@]}"; do echo "     • $w"; done
    for p in "${FAILED_PKGS[@]}"; do echo "     • pip: $p non installato"; done
fi

echo
echo "   Modelli ollama installati:"
ollama list 2>/dev/null | tail -n +2 | while IFS= read -r l; do echo "     $l"; done \
    || echo "     (ollama non disponibile)"
echo
if [ -f "$HOME/piper-voices/it_IT-riccardo-x_low.onnx" ]; then
    echo "   🎤 Voce TTS: piper neurale (it_IT-riccardo-x_low) ✅"
else
    echo "   🎤 Voce TTS: espeak-ng (piper non disponibile)"
fi

echo
echo "   OS Rilevato: $OS_PRETTY"
if [ "$IS_TRIXIE" = true ]; then
    echo "   ℹ️  Debian trixie — alcuni errori apt sono attesi e non bloccanti"
fi

echo
echo "   ⚠️  REBOOT NECESSARIO per:"
echo "       - I2S audio (HifiBerry DAC)"
echo "       - I2C (MPU6050 edge detection)"
echo "       sudo reboot"
echo
echo "   Dopo il reboot:"
echo "   Avvio:       bash $CORE_DIR/scripts/start.sh"
echo "   Dashboard:   http://$(hostname -I 2>/dev/null | awk '{print $1}' || echo '<ip>'):5000"
echo "   Log:         $LOG"
echo "════════════════════════════════════════════════════════"
