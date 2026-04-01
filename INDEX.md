# 📚 Spooky — Documentazione Completa

Benvenuto in Spooky! Qui trovi la guida per installare, configurare e usare il robot.

---

## 🚀 Inizia Qui

**Nuovo al progetto?** Segui questo percorso:

1. **[README.md](README.md)** — Panoramica e quick start
2. **[SETUP.md](SETUP.md)** — Installazione dettagliata per sistema
3. **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Se qualcosa non funziona

---

## 📖 Documentazione per Sistema

### 🍎 macOS (Sviluppo)

```bash
# Setup automatico (recommended)
bash robot-core/scripts/install_mac.sh

# Setup manuale
make setup-mac
```

### 🦾 Raspberry Pi 5 (Production)

```bash
# Setup automatico (completo)
bash robot-core/scripts/install_rpi.sh

# Servizio systemd auto-boot
sudo systemctl start spooky
sudo journalctl -u spooky -f
```

---

## 🎮 Comandi Rapidi

### Sviluppo (con Makefile)

```bash
make help              # Lista tutti i comandi
make setup-mac         # Setup su macOS
make ollama            # Setup Ollama + modelli
make run               # Avvia Spooky
make debug             # Avvia con logging DEBUG
make diagnose          # Verifica installazione
make test              # Esegui unit tests
make clean             # Pulisci cache
```

### Manuale

```bash
# Avvia Ollama (non è necessario se auto-avviato)
ollama serve

# Avvia Spooky (da robot-core/)
python main.py --debug

# Diagnostica
python scripts/diagnose_system.py

# Iscrivi nuovo volto
python scripts/enroll_face.py

# Visione debug
python scripts/diagnose.py
```

---

## 🏗️ Architettura

### Due Modelli AI Indipendenti

```
📷 MODELLO VISIVO                    🧠 MODELLO TESTO
└─ Moondream (3.5 GB)               └─ Llama3.2:3b (2 GB)
└─ Riconosce: oggetti, scene        └─ Ragiona: decisioni, dialogo
└─ Unload dopo analisi               └─ Sempre caricato
└─ Corre: ogni 120s                 └─ Istantaneo
```

### Event Bus Pub/Sub

Tutti i servizi comunicano SOLO tramite il bus — nessun accoppiamento diretto:

```
Camera → FRAMES → Vision → OBJECTS_DETECTED → Mind → DIALOG → Audio
                    ↓
                Face DB ← recognizer
```

---

## 📁 Struttura Cartelle

```
spooky/
├── README.md                    ← Inizio qui
├── SETUP.md                     ← Setup dettagliato
├── TROUBLESHOOTING.md           ← Risoluzione problemi
├── Makefile                     ← Comandi rapidi
├── requirements.txt             ← Dependenze generali
│
└── robot-core/                  ← Codice principale
    ├── main.py                  ← Entry point
    ├── requirements.txt         ← Dipendenze robot
    ├── config/
    │   ├── robot.yaml           ← Config principale
    │   ├── local.yaml           ← Override locale (gitignore)
    │   └── local.yaml.example   ← Template
    ├── core/
    │   ├── bus.py               ← Event bus pub/sub
    │   ├── config.py            ← Config loader
    │   ├── logger.py            ← Setup logging
    │   ├── modes.py             ← State machine
    │   └── safety.py            ← Watchdog hardware
    ├── services/
    │   ├── vision.py            ← Camera + face + moondream
    │   ├── mind.py              ← LLM reasoning
    │   ├── motor.py             ← Servo controller
    │   ├── audio.py             ← Vosk + TTS
    │   ├── memory.py            ← Persistent DB
    │   └── ... (10+ servizi)
    ├── skills/
    │   ├── patrol.py
    │   ├── track_face.py
    │   └── idle_behavior.py
    ├── scripts/
    │   ├── install_mac.sh        ← Setup macOS
    │   ├── install_rpi.sh        ← Setup RPi
    │   ├── setup_ollama.sh       ← Setup modelli AI
    │   ├── start.sh              ← Avvio manuale
    │   ├── enroll_face.py        ← Iscrivi volto
    │   ├── diagnose_system.py    ← Diagnostica
    │   └── spooky.service        ← Systemd unit
    ├── logs/                     ← Log runtime
    ├── data/
    │   ├── memory.db             ← Database memoria
    │   ├── faces/                ← Volti iscritti
    │   └── snapshots/            ← Screenshot debug
    └── venv/                     ← Virtual environment Python
```

---

## 🔧 Configurazione

### Parametri Critici (robot.yaml)

```yaml
# Modelli AI
llm:
  model_priority: ["llama3.2:3b", ...]         # Testo

vision_llm:
  model_priority: ["moondream", "llava:7b"]    # Immagini
  keep_alive_s: 0                              # Unload dopo uso
  scene_interval_s: 120                        # Frequenza analisi

# Riconoscimento visi
face:
  confidence_high: 0.80          # "Certo è X"
  confidence_mid: 0.55           # "Potrebbe essere X?"

# Safety (RPi)
safety:
  max_speed: 60                  # % velocità motori
  max_cpu_temp_c: 80.0           # Shutdown se caldo
```

### Override Locale (local.yaml)

Copia `local.yaml.example` → `local.yaml` e modifica parametri per il tuo setup.

Non viene mai committato (in .gitignore).

---

## 🧪 Testing

```bash
# Unit tests
cd robot-core
python -m pytest tests/ -v

# Diagnostica completa
python scripts/diagnose_system.py

# Testa modelli Ollama
ollama run moondream "Describe this scene"
ollama run llama3.2:3b "What is AI?"
```

---

## 🚦 Modalità di Funzionamento

```
SHUTDOWN ─→ BOOT
  ↓
STARTUP (carica config, connette servizi)
  ↓
IDLE (attende comandi)
  ↓
┌─ COMPANION_DAY   (segue + parla di giorno)
├─ NIGHT_WATCH     (sorveglia di notte, alert su movimento)
├─ PLAY            (modalità gioco interattiva)
├─ TRACKING        (segue il volto di una persona)
└─ LEARNING        (appredi comportamenti)
```

---

##📞 Supporto

### Problemi comuni

→ Vedi **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)**

Esempio rapido:
```bash
# Diagnostica
python robot-core/scripts/diagnose_system.py

# Debug full output
cd robot-core && python main.py --debug 2>&1 | head -200
```

### Logs

```bash
# Spooky log
tail -50 robot-core/logs/spooky.log

# Ollama log
tail -50 robot-core/logs/ollama.log

# Systemd log (RPi)
sudo journalctl -u spooky -n 100 -f
```

---

## 🤝 Prossimi Passi

- [ ] Setup completo (make setup-mac / install_rpi.sh)
- [ ] Verifica: make diagnose
- [ ] Iscrivi un volto: python scripts/enroll_face.py
- [ ] Test avvio: python main.py --debug
- [ ] Leggi architettura: robot-core/architecture.md
- [ ] Configura locale: robot-core/config/local.yaml
- [ ] Porta in production: systemd service su RPi

---

## 📊 Telemetria

**Nessuna telemetria.** Tutti i dati restano sul dispositivo locale.

- ✅ Nessun cloud
- ✅ Nessun tracking
- ✅ Nessun upload foto/video
- ✅ Database locale (data/memory.db)

---

## 📝 License

Vedi LICENSE file per dettagli.

---

**Domande?** Apri una issue su GitHub o consulta la documentazione dettagliata nelle cartelle.

Buon divertimento con Spooky! 🕷️
