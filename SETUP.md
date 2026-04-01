# 🕷️ Spooky — Guida di Installazione Completa

## Architettura: Dual-Model Vision + Reasoning

```
📷 Moondream (Modello Visivo)       🧠 Llama3.2:3b (Modello di Pensiero)
   └─ Riconosce oggetti/scene          └─ Ragiona e comunica
   └─ 3.5 GB RAM                       └─ 2.0 GB RAM
   └─ Corre ogni 120s                  └─ Sempre disponibile
```

---

## Setup Rapido (Tutti i Sistemi)

### 1. Installa Ollama

**macOS:**
```bash
brew install ollama
```

**Linux/RPi:**
```bash
curl -fsSL https://ollama.ai/install.sh | sh
```

### 2. Scarica i Modelli

```bash
ollama pull llama3.2:3b   # Modello di testo (2 GB)
ollama pull moondream     # Modello di visione (3.5 GB)
```

Verifica:
```bash
ollama list
```

Dovresti vedere entrambi i modelli.

### 3. Avvia il Server Ollama

**Terminal 1 — Server Ollama:**
```bash
ollama serve
```

Dovrà rimanere in esecuzione continuo.

---

## Setup Specifico per Sistema

### macOS (Sviluppo)

```bash
cd /Users/herl11e5/Desktop/Projects/spooky

# 1. Crea virtual environment
python3 -m venv venv
source venv/bin/activate

# 2. Installa dipendenze
pip install -r robot-core/requirements.txt

# 3. Crea cartelle dati
mkdir -p robot-core/{logs,data/faces,data/snapshots}

# 4. Copia config di esempio
cp robot-core/config/local.yaml.example robot-core/config/local.yaml
# Edita local.yaml se necessario (override rispetto a robot.yaml)

# 5. Avvia Spooky
cd robot-core
python main.py --debug
```

**Verifica in un altro Terminal:**
```bash
# Controlla che Ollama sia accessibile
python3 -c "import ollama; models = [m.model for m in ollama.list().models]; print('Modelli:', models)"
```

---

### Raspberry Pi 5 (RPi OS Bookworm)

```bash
# 1. Esegui lo script di installazione completo
bash ~/spooky/robot-core/scripts/install_rpi.sh

# Questo installa automaticamente:
# - Python 3.11+
# - Pacchetti apt (GPIO, audio, video, etc.)
# - Virtual environment con pip packages
# - robot-hat di SunFounder
# - vilib per la visione USB
# - systemd service (spooky.service)

# 2. Dopo l'installazione, avvia il servizio
sudo systemctl start spooky
sudo systemctl enable spooky   # Auto-avvio al boot

# 3. Monitora i log
journalctl -u spooky -f
```

---

## Verifica Installazione

### ✅ Checklist

```bash
# 1. Ollama in esecuzione?
curl -s http://localhost:11434 && echo "✅ Ollama OK" || echo "❌ Ollama offline"

# 2. Modelli disponibili?
ollama list | grep -E "moondream|llama3.2:3b"

# 3. Python packages?
cd robot-core && python -c "import cv2, ollama, vosk, sounddevice, flask; print('✅ Tutti i packages importabili')"

# 4. Cartelle dati?
[ -d robot-core/data/faces ] && echo "✅ data/faces OK" || echo "❌ Crea: mkdir -p robot-core/data/faces"

# 5. Configurazione OK?
python main.py --debug 2>&1 | head -20   # Controlla i log iniziali
```

---

## Risoluzione Problemi

### Moondream non riconosce oggetti

**Causa:** Ollama offline o modello non caricato.

**Soluzione:**
```bash
# Terminal 1: Controlla ollama
ollama serve

# Terminal 2: Ricarica modelli
ollama pull moondream
ollama pull llama3.2:3b

# Terminal 3: Testa il modello
python3 -c "import ollama; resp = ollama.chat(model='moondream', messages=[{'role': 'user', 'content': 'test', 'images': ['']}])"
```

### RAM insufficiente (RPi)

Se vedi "RAM < 1200MB" nei log:
- Moondream richiede 3.5 GB
- Llama3.2:3b richiede 2.0 GB  
- Totale picco: ~5.5 GB

**Su RPi 8GB:**
- Aumenta swap: `echo "CONF_SWAPSIZE=2048" | sudo tee /etc/dphys-swapfile`
- Ri-avvia: `sudo reboot`
- Monitora: `free -h`

### Microfono non riconosce

```bash
# Scansiona device audio
python3 -c "import sounddevice; print(sounddevice.query_devices())"

# Edita robot-core/config/local.yaml:
audio:
  mic_device: 2   # Numero dal output sopra
```

### GPIO errori su RPi

```bash
# Assicurati di essere nel gruppo gpio
sudo usermod -aG gpio $(whoami)

# Re-login richiesto
newgrp gpio
```

---

## Struttura Cartelle Creata

```
robot-core/
├── logs/            ← Log Spooky, Ollama, system
├── data/
│   ├── memory.db    ← Database memoria persistente
│   ├── faces/       ← Volti iscritti (embedding LBPH)
│   └── snapshots/   ← Screenshot per debugging
├── config/
│   ├── robot.yaml          ← Configurazione principale
│   ├── local.yaml          ← Override locale (gitignore)
│   └── local.yaml.example  ← Template
└── venv/            ← Virtual environment Python
```

---

## Configurazione Best-Practice

### macOS Development

**robot-core/config/local.yaml:**
```yaml
robot:
  name: "Spooky-Dev"

logging:
  level: "DEBUG"

vision_llm:
  model_priority: ["moondream", "llava:7b"]
  keep_alive_s: 0          # Unload immediato → libera RAM
  scene_interval_s: 30     # Più frequente per debug
  object_interval_s: 45

llm:
  model_priority: ["llama3.2:3b"]
  keep_alive_s: 180        # Keep loaded su 8GB
```

### RPi Production

**robot-core/config/local.yaml:**
```yaml
robot:
  name: "Spooky-RPi"

logging:
  level: "INFO"
  file: "/var/log/spooky.log"

vision_llm:
  model_priority: ["moondream"]
  scene_interval_s: 120    # Risparmia CPU
  object_interval_s: 150

llm:
  model_priority: ["llama3.2:3b"]
  keep_alive_s: 0          # Libera RAM quando idle
```

---

## Comandi Utili

```bash
# Avvia server Ollama
ollama serve

# Scarica model senza avvio
ollama pull moondream

# Lista modelli
ollama list

# Testa modello
ollama run moondream

# Logs Spooky (macOS)
tail -f robot-core/logs/spooky.log

# Logs Spooky (RPi)
journalctl -u spooky -f

# Restart servizio (RPi)
sudo systemctl restart spooky

# Stop robot
pkill -f "python.*main.py"
```

---

## Step Successivi

- [ ] Iscrivi i volti con `scripts/enroll_face.py`
- [ ] Testa vision con `scripts/diagnose.py`
- [ ] Configura le skill (patrol, track_face, idle_behavior)
- [ ] Crea una modalità personalizzata in `core/modes.py`
