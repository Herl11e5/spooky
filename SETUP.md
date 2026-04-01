# 🕷️ Setup Spooky — RPi 5 Dettagliato

## Installazione (One-Shot)

```bash
cd ~/spooky
bash robot-core/scripts/install_rpi.sh
```

**Questo installa:**
- Python 3.11+ + venv
- Ollama + modelli (moondream 3.5GB, llama3.2:3b 2GB)
- picamera2 + libcamera
- robot-hat v2.0 (SunFounder)
- Vosk modello Italiano
- I2C/SPI abilitati
- Systemd service

**Tempo:** ~30-45 minuti

---

## Verifica

```bash
python robot-core/scripts/diagnose_system.py
```

Deve mostrare ✅ su tutti i parametri.

---

## Configurazione Local

**robot-core/config/local.yaml:**

```yaml
robot:
  name: "Spooky-RPi5"

logging:
  level: "INFO"

vision_llm:
  scene_interval_s: 120
  object_interval_s: 150
  keep_alive_s: 0          # Libera RAM dopo uso

llm:
  keep_alive_s: 0          # Libera RAM quando idle
```

Modifica secondo le tue esigenze.

---

## Avvio

```bash
# Automatico (al boot)
sudo systemctl start spooky
sudo systemctl enable spooky

# Manuale (debug)
sudo systemctl stop spooky
cd robot-core && python main.py --debug
```

---

## Monitora

```bash
# Log in tempo reale
sudo journalctl -u spooky -f

# Status
sudo systemctl status spooky

# Diagnostica
python robot-core/scripts/diagnose_system.py
```

---

## Problemi Comuni

Vedi **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)**
