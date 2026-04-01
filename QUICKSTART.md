# 🎉 Spooky — Installazione RPi 5

⚠️ **SOLO Raspberry Pi 5 (8 GB RAM)**

---

## 🚀 Primo Avvio (3 Comandi)

### Su Raspberry Pi (SSH o Fisico)

```bash
# 1. Clona il repo
git clone https://github.com/Herl11e5/spooky.git ~/spooky
cd ~/spooky

# 2. Setup automatico (30-45 min)
# Installa: Python 3.11, Ollama, GPIO, I2C/SPI, robot-hat, systemd
bash robot-core/scripts/install_rpi.sh

# 3. Avvio automatico al reboot
sudo systemctl start spooky
sudo systemctl enable spooky

# Monitora log
sudo journalctl -u spooky -f
```

**Fatto!** Spooky è ora attivo e parte automaticamente al riavvio.

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

## 🔥 Dopo l'Installazione

1. Verifica: `python robot-core/scripts/diagnose_system.py`
2. Monitora: `sudo journalctl -u spooky -f`
3. Test: Apri dashboard su `http://<ip-rpi>:5000`

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

## ✨ Cosa è Pronto su RPi

✅ **Setup Completo:**
- Python 3.11+ + venv
- Ollama + modelli AI
- GPIO + robot-hat SunFounder
- I2C/SPI abilitati
- Systemd service (auto-boot)

✅ **Doppio Modello AI:**
- Moondream (visione 3.5 GB)
- Llama3.2:3b (ragionamento 2 GB)
- Memory optimization (unload automatico)
- Event bus pub/sub

✅ **Diagnostica + Documentazione:**
- Sistema di verifica completo
- Troubleshooting
- Config predefinite

---

**Pronto per il decollo!**

```bash
bash robot-core/scripts/install_rpi.sh
sudo systemctl restart spooky
sudo journalctl -u spooky -f
```

Buona fortuna con Spooky! 🕷️
