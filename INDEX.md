# 📚 Spooky — Documentazione Essenziale

> **Robot autonomo su Raspberry Pi 5 con Moondream (visione) + Llama3.2:3b (ragionamento)**

---

## 🚀 Quick Setup

**Su RPi 5:**

```bash
git clone https://github.com/Herl11e5/spooky.git ~/spooky
cd ~/spooky
bash robot-core/scripts/install_rpi.sh
sudo systemctl start spooky
```

---

## 📖 Documentazione

- **[README.md](README.md)** — Panoramica + comandi base
- **[SETUP.md](SETUP.md)** — Installazione dettagliata
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Debug & problemi

---

## 🛠️ File Chiave

```
robot-core/
├── scripts/
│   ├── install_rpi.sh          # Installer (unico per RPi)
│   ├── diagnose_system.py      # Diagnostica
│   ├── enroll_face.py          # Iscrivi volti
│   └── diagnose.py             # Debug visione
├── main.py                      # Entry point
├── config/
│   ├── robot.yaml              # Configurazione
│   └── local.yaml.example      # Override template
├── core/
│   ├── bus.py                  # Event bus
│   ├── config.py               # Config loader
│   ├── logger.py               # Logging
│   ├── modes.py                # State machine
│   └── safety.py               # Watchdog
├── services/
│   ├── vision.py               # Moondream + face detection
│   ├── mind.py                 # Llama3.2:3b reasoning
│   ├── motor.py                # SunFounder control
│   ├── audio.py                # Vosk + TTS
│   └── ... (10+ servizi)
└── logs/                        # Runtime logs
```

---

## ⚙️ Comandi Base

```bash
# Monitora
sudo journalctl -u spooky -f

# Restart
sudo systemctl restart spooky

# Debug
sudo systemctl stop spooky && python robot-core/main.py --debug

# Diagnostica
python robot-core/scripts/diagnose_system.py

# Nuovi volti
python robot-core/scripts/enroll_face.py
```

---

## 🆘 Problemi

→ Vedi **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)**
