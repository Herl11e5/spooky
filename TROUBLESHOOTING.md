# 🔧 TROUBLESHOOTING — Spooky

Risoluzione rapida dei problemi più comuni.

---

## ❓ Problemi di Setup

### Ollama non installa / non parte

**Sintomo:**
```
❌ Ollama offline — esegui: ollama serve
```

**Cause e soluzioni:**

1. **Ollama non è installato**
   ```bash
   # macOS
   brew install ollama
   
   # Linux
   curl -fsSL https://ollama.ai/install.sh | sh
   ```

2. **Ollama non parte**
   ```bash
   # Prova avvio manuale
   ollama serve
   
   # Se da errore, controlla permessi
   sudo chown -R $USER ~/.ollama
   ollama serve
   ```

3. **Porta 11434 già occupata**
   ```bash
   # Uccidi processo occupante
   lsof -i :11434
   kill -9 <PID>
   
   # Poi riavvia
   ollama serve
   ```

4. **Memoria insufficiente**
   ```bash
   # Aumenta swap (RPi)
   echo "CONF_SWAPSIZE=2048" | sudo tee /etc/dphys-swapfile
   sudo reboot
   ```

---

### Modelli non scaricano / timeout

**Sintomo:**
```
❌ moondream — download timeout
```

**Soluzioni:**

1. **Prova scarico manuale con retry**
   ```bash
   # Download con verbose
   ollama pull moondream -v
   
   # Se timeout, riprova (riprenderà da dove si è fermato)
   ollama pull moondream
   ```

2. **Velocizza con modello alternativo**
   ```bash
   # Se moondream è lento, usa llava più leggero
   ollama pull llava
   ```

3. **Controlla spazio disco**
   ```bash
   df -h
   # Moondream = 3.5 GB
   # Llama3.2:3b = 2 GB
   # Totale consigliato: 8 GB liberi
   ```

---

### Python venv non funziona

**Sintomo:**
```
❌ venv non trovato
```

**Soluzione:**
```bash
# Ricrea venv from scratch
rm -rf robot-core/venv
python3 -m venv robot-core/venv
source robot-core/venv/bin/activate
pip install -r robot-core/requirements.txt
```

---

## ❓ Problemi di Runtime

### Visione riconosce solo persone

**Sintomo:**
```
👁️  Vedo una persona
🔍 Oggetti: (nulla)
```

**Cause:**

1. **Moondream non è caricato**
   ```bash
   # Verifica
   ollama list | grep moondream
   
   # Se manca, scarica
   ollama pull moondream
   ```

2. **RAM insufficiente**
   ```bash
   # Controlla RAM disponibile
   free -h  # Linux
   vm_stat  # macOS
   
   # Se < 1200 MB, i modelli visivi non partono
   # Chiudi altri programmi o aumenta swap
   ```

3. **Ollama offline**
   ```bash
   # Verifica
   curl http://localhost:11434
   
   # Se offline, avvia
   ollama serve
   ```

**Soluzione rapida:**
1. Terminal 1: `ollama serve`
2. Terminal 2: `bash robot-core/scripts/setup_ollama.sh`
3. Terminal 3: Avvia Spooky e ripeti visione

---

### "Modello non trovato" durante runtime

**Sintomo:**
```
WARNING: vision_model not found
```

**Soluzione:**
```bash
# Verifica modelli disponibili
ollama list

# Scarica mancanti
ollama pull moondream
ollama pull llama3.2:3b

# Riavvia Spooky
pkill -f "python.*main.py"
cd robot-core && python main.py --debug
```

---

### Spooky parte ma non vede la camera

**Sintomo:**
```
⚠️  Camera: no hardware — simulation mode
```

**Cause:**

1. **Sviluppo su macOS/Linux (expected)**
   - È normale! Usa camera USB se disponibile
   - Testing su RPi con Pi Camera

2. **RPi: camera non abilitata**
   ```bash
   # Abilita camera
   raspi-config
   # → Interface Options → Camera → Enable
   # → Reboot
   ```

3. **RPi: permessi camera**
   ```bash
   # Aggiungi utente al gruppo video
   sudo usermod -aG video $USER
   # → Logout/Login richiesto
   ```

---

### Microfono non ascolta / TTS non parla

**Sintomo:**
```
⚠️  Audio: no microphone detected
```

**Cause:**

1. **Scopri dispositivi audio disponibili**
   ```bash
   python3 -c "import sounddevice; print(sounddevice.query_devices())"
   ```

2. **Specifica device manualmente**
   ```yaml
   # robot-core/config/local.yaml
   audio:
     mic_device: 2      # numero dal output sopra
   ```

3. **Testa audio**
   ```bash
   # Registra 3 secondi
   arecord -d 3 test.wav
   ```

---

### Errori GPIO su RPi

**Sintomo:**
```
❌ Cannot get GPIO line information
```

**Cause:**

1. **Utente non nel gruppo gpio**
   ```bash
   sudo usermod -aG gpio $USER
   newgrp gpio
   # o logout/login
   ```

2. **I2C/SPI non abilitati**
   ```bash
   sudo raspi-config
   # → Interface Options → I2C/SPI → Enable
   # → Reboot
   ```

3. **robot-hat non installato**
   ```bash
   pip install robot-hat
   ```

---

## ❓ Problemi di Performance

### CPU al 100% / Spooky lento

**Cause:**

1. **Modelli in analisi contemporaneamente**
   - Fix: Vision unoaccs Moondream dopo ogni analisi
   - Configura: `keep_alive_s: 0` in config

2. **Face detection troppo frequente**
   ```yaml
   # Slaccia frequency
   camera:
     face_detect_interval_s: 0.5  # da 0.25
   ```

3. **LLM non converge**
   ```yaml
   # Riduci tokens
   llm:
     max_tokens: 100  # da 200
   ```

---

### RAM sempre piena / OOM

**Diagnosi:**
```bash
# Monitora memoria in tempo reale
watch -n 1 free -h
```

**Soluzioni:**

1. **Unload modelli più aggressivamente**
   ```yaml
   llm:
     keep_alive_s: 10    # scarica dopo 10s inattività (da 180)
   vision_llm:
     keep_alive_s: 0     # scarica subito dopo analisi
   ```

2. **Riduci batch size / risoluzione camera**
   ```yaml
   vision_llm:
     image_width: 160    # da 320
     image_height: 120   # da 240
   ```

3. **Su RPi, aumenta swap**
   ```bash
   echo "CONF_SWAPSIZE=2048" | sudo tee /etc/dphys-swapfile
   sudo systemctl restart dphys-swapfile
   ```

---

## ❓ Problemi di Test

### pytest non importa/run

**Sintomo:**
```
❌ pytest: command not found
```

**Soluzione:**
```bash
# Installa pytest
pip install pytest

# Esegui test
cd robot-core
python -m pytest tests/ -v
```

---

## 🆘 Debug Avanzato

### Abilita DEBUG logging

```bash
cd robot-core
python main.py --debug 2>&1 | tee logs/spooky_debug.log

# Oppure in config
logging:
  level: "DEBUG"
```

### Controlla database volti

```bash
python3 << 'EOF'
from pathlib import Path
from services.vision import FaceDatabase

db = FaceDatabase(Path("data/faces"))
persons = db.all_persons()
print(f"Volti iscritti: {len(persons)}")
for pid, name in persons.items():
    print(f"  - {name} ({pid})")
EOF
```

### Testa modelli Ollama direttamente

```bash
# Testo modello di testo
ollama run llama3.2:3b "Dimmi una barzelletta"

# Test modello visione
ollama run moondream "Describe object: apple"
```

---

## 📊 Checklist Diagnostica Completa

```bash
# Quick diagnostic
cd robot-core
python scripts/diagnose_system.py

# Full checks
✅ Ollama online?
   curl -s http://localhost:11434 && echo "OK" || echo "OFFLINE"

✅ Modelli disponibili?
   ollama list | grep -E "llama3.2|moondream"

✅ Python packages OK?
   python -c "import cv2, ollama, vosk, sounddevice; print('OK')"

✅ Configurazione OK?
   python -c "from core.config import load_config; load_config('config/robot.yaml')"

✅ Camera OK?
   python -c "from services.vision import CameraBackend; c=CameraBackend(); print(c.open())"

✅ Volti OK?
   ls -la data/faces/*/samples/ 2>/dev/null || echo "Nessun volto iscritto"
```

---

## 📞 Se il problema persiste

1. **Controlla i log**
   ```bash
   tail -100 robot-core/logs/spooky.log
   tail -100 robot-core/logs/ollama.log
   ```

2. **Riporta con output di `diagnose_system.py`**
   ```bash
   cd robot-core && python scripts/diagnose_system.py
   ```

3. **Apri issue su GitHub** con:
   - Output del diagnose
   - Log snippet
   - Hardware utilizzato
   - Sistema operativo
