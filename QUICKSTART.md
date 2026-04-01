# 🎉 Setup Completato — Spooky Installation Package

Tutti i file di installazione e documentazione sono ora **pronti e sistemati**.

---

## 📋 Di Cosa Ho Creato/Aggiornato

### 📖 Documentazione

| File | Scopo |
|---|---|
| **[INDEX.md](INDEX.md)** | 🎯 **INIZIO QUI** — Indice e guida completa |
| **[README.md](README.md)** | Panoramica + quick start (macOS + RPi) |
| **[SETUP.md](SETUP.md)** | Setup dettagliato per ogni sistema |
| **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** | Risoluzione problemi comuni |

### 🛠️ Script di Installazione

| Script | Scopo | Sistema |
|---|---|---|
| **install_mac.sh** | Setup completo: Python, Ollama, dipendenze | macOS |
| **install_rpi.sh** | Setup RPi: GPIO, robot-hat, systemd service | RPi 5 |
| **setup_ollama.sh** | Scarica modelli AI (moondream + llama3.2:3b) | Tutti |
| **start.sh** | Avvio manuale Spooky | Tutti |
| **diagnose_system.py** | Verifica completa installazione | Tutti |

### ⚙️ Configurazione

| File | Aggiornamento |
|---|---|
| **Makefile** | 📝 NUOVO — Comandi rapidi (make setup-mac, make run, etc.) |
| **local.yaml.example** | Aggiornato con commenti dettagliati |

---

## 🚀 Primo Avvio (macOS)

### Passo 1: Setup Automatico (5-10 min)

```bash
cd /Users/herl11e5/Desktop/Projects/spooky

# Rendi eseguibili gli script
chmod +x robot-core/scripts/*.sh

# Setup automatico (Python, pip, Ollama client)
bash robot-core/scripts/install_mac.sh
```

**Questo installa:**
- ✅ Python 3.11+ (via Homebrew)
- ✅ Ollama client (via Homebrew)
- ✅ Virtual environment + npm packages
- ✅ Cartelle dati (logs/, data/faces/, data/snapshots/)

### Passo 2: Setup Ollama Server + Modelli (10-30 min)

```bash
# Terminal 1: Scarica modelli AI
bash robot-core/scripts/setup_ollama.sh

# Questo: scarica moondream (3.5 GB) + llama3.2:3b (2 GB) 
# Se è la prima volta, ci vuole parecchio tempo
```

### Passo 3: Avvio

```bash
# Terminal 1: Ollama server (mantieni aperto)
ollama serve

# Terminal 2: Spooky
source robot-core/venv/bin/activate
cd robot-core
python main.py --debug

# Terminal 3: Browser
open http://localhost:5000
```

Vedrai i log come:
```
👁️  Vedo una persona e un gatto
🔍 Oggetti: persona, gatto
🧠 Miao! Mi piacciono i gatti!
```

---

## 🦾 Per Raspberry Pi

### Installazione One-Shot

```bash
# Su RPi (connesso via SSH o fisicamente)
cd ~/spooky

# Setup completo — disattivato automatico GPU, I2C, SPI, systemd service
bash robot-core/scripts/install_rpi.sh

# Al termine, il servizio partirà automaticamente al boot
sudo systemctl status spooky
```

---

## 📊 Comandi Utili (Makefile)

Usa il **Makefile** per comandi rapidi:

```bash
make help              # Lista comandi
make setup-mac         # Setup macOS
make ollama            # Setup Ollama
make run               # Avvia Spooky
make debug             # Debug mode
make diagnose          # Verifica installazione
make test              # Unit tests
make clean             # Pulisci cache
```

---

## ✅ Verifica Installazione

```bash
# Esegui diagnosi completa
cd robot-core
python scripts/diagnose_system.py
```

Dovrebbe mostrare:
```
✅ Python 3.11+
✅ Tutti i packages (cv2, ollama, vosk, sounddevice, flask)
✅ Ollama online (http://localhost:11434)
✅ moondream disponibile
✅ llama3.2:3b disponibile
✅ Cartelle dati create
✅ venv OK
```

---

## 🎯 Architettura (Recap)

Due modelli AI separati:

```
📷 Moondream (Vision)                🧠 Llama3.2:3b (Reasoning)
   └─ Corre ogni 120s                  └─ Sempre pronto
   └─ 3.5 GB RAM per 30-40s           └─ 2 GB RAM costanti
   └─ Riconosce: ✅ oggetti, scene     └─ Ragiona: ✅ decisioni
   └─ Unload dopo uso                  └─ Keep-alive 180s
```

**Flusso:**
1. Fotogramma camera
2. Moondream → "Vedo una persona e una pianta"
3. Llama3.2:3b → "Potrei prendermi cura della pianta"
4. Audio → comunica al robot

---

## 📚 Documentazione Completa

- **[INDEX.md](INDEX.md)** — Punto di partenza (indice completo)
- **[SETUP.md](SETUP.md)** — Installazione sistema per sistema
- **[README.md](README.md)** — Panoramica veloce
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Problemi comuni
- **robot-core/architecture.md** — Architettura dettagliata del tuo robot

---

## 🔥 Prossimo Passo per Te

1. Esegui: `make setup-mac` (o `bash robot-core/scripts/install_mac.sh`)
2. Esegui: `bash robot-core/scripts/setup_ollama.sh` (scarica modelli)
3. Verifica: `python robot-core/scripts/diagnose_system.py`
4. Avvia: `make run` (o `ollama serve` + `python main.py --debug`)
5. Testa: Vai su browser → `http://localhost:5000`

---

## 📞 Se Qualcosa Non Funziona

1. **Verifica con diagnostica:**
   ```bash
   python robot-core/scripts/diagnose_system.py
   ```

2. **Leggi TROUBLESHOOTING.md:**
   - Moondream non riconosce oggetti → [qui](TROUBLESHOOTING.md#visione-riconosce-solo-persone)
   - Ollama non installa → [qui](TROUBLESHOOTING.md#ollama-non-installa--non-parte)
   - CPU al 100% → [qui](TROUBLESHOOTING.md#cpu-al-100--spooky-lento)

3. **Debug mode:**
   ```bash
   cd robot-core && python main.py --debug 2>&1 | tail -50
   ```

---

## 🎓 File Utili per Capire il Progetto

- `robot-core/main.py` — Entry point, boot ordinato servizi
- `robot-core/core/bus.py` — Event bus pub/sub
- `robot-core/services/vision.py` — Visione (Moondream + face detection)
- `robot-core/services/mind.py` — LLM reasoning (Llama3.2:3b)
- `robot-core/config/robot.yaml` — Configurazione principale

---

## ✨ Recap: Cosa È Pronto

✅ **macOS Development:**
- One-shot install script
- Ollama setup automatico
- Makefile comandi rapidi
- Diagnosi completa

✅ **Raspberry Pi Production:**
- Setup RPi completo (GPIO, I2C, SPI, systemd)
- Auto-boot configurato
- Script manuale fallback

✅ **Documentazione:**
- Setup guide per ogni sistema
- Troubleshooting completo
- Architecture docs
- Configurazione sample

✅ **Dual-Model AI:**
- Moondream visione (con fallback sintetico)
- Llama3.2:3b ragionamento
- RAM optimization
- Event bus pub/sub

---

**Sei pronto! Inizia con:**

```bash
cd /Users/herl11e5/Desktop/Projects/spooky
bash robot-core/scripts/install_mac.sh
bash robot-core/scripts/setup_ollama.sh
make run
```

Buona fortuna con Spooky! 🕷️
