# 🔧 Troubleshooting: Installazione su Debian trixie (RPi 5)

**Situazione**: Lo script `install_rpi.sh` sta riscontrando alcuni pacchetti apt non disponibili su Debian trixie (testing) invece di RPi OS Bookworm.

---

## ❌ Errori Riscontrati

```
E: Unable to locate package libgpiod2
E: Unable to locate package python3-gpiod
E: Package 'libatlas-base-dev' has no installation candidate
```

## 🤔 Perché Succede?

| Pacchetto | Motivo | Stato |
|-----------|--------|-------|
| **libgpiod2** | Non in Debian trixie (testing repo) | ⚠️ Opzionale |
| **python3-gpiod** | Stesso motivo | ⚠️ Opzionale |
| **libatlas-base-dev** | Obsoleto, sostituito da libopenblas-dev | ✅ Sostituito |

**Nota**: Robot-hat ha il suo GPIO library integrato, quindi questi pacchetti NON sono critici.

---

## ✅ Soluzione

Ho aggiornato `install_rpi.sh` per:

1. **Rilevare automaticamente la versione Debian**
   ```bash
   IS_BOOKWORM=false
   IS_TRIXIE=false
   [ "$OS_VERSION" = "12" ] && IS_BOOKWORM=true
   [ "$OS_VERSION" = "13" ] && IS_TRIXIE=true
   ```

2. **Rendere opzionali i pacchetti non disponibili su trixie**
   ```bash
   OPT_PKGS=()
   if [ "$IS_BOOKWORM" = true ]; then
       OPT_PKGS+=(python3-gpiod libgpiod2)
   elif [ "$IS_TRIXIE" = true ]; then
       warn "python3-gpiod e libgpiod2 non disponibili su trixie"
   fi
   ```

3. **Rimuovere libatlas-base-dev (obsoleto)**
   - Sostituito con `libopenblas-dev` (già installato ✓)

4. **Chiarire i warnings nel summary finale**
   ```
   OS Rilevato: Debian GNU/Linux 13 (trixie)
   ℹ️  Debian trixie — alcuni errori apt sono attesi e non bloccanti
   ```

---

## 🚀 Cosa Fare Ora

### Opzione 1: Re-run dello script (CONSIGLIATO)

```bash
cd /home/spooky/spooky/robot-core
bash scripts/install_rpi.sh
```

Lo script ora:
- Riconoscerà Debian trixie
- Salterà i pacchetti non disponibili
- Installerà quelli essenziali
- Mostra un summary chiaro

### Opzione 2: Continuare con il sistema attuale

Se l'installazione è già progredita, i warnings sono **non bloccanti**. Verifica che siano disponibili:

```bash
# Test imporazioni critiche
python3 -c "import cv2; print('✓ OpenCV')"
python3 -c "import numpy; print('✓ NumPy')"
python3 -c "import robot_hat; print('✓ robot-hat')"
python3 -c "import ollama; print('✓ Ollama')"
```


---

## 📋 Stato Attuale della Vostra Installazione

| Componente | Status | Note |
|-----------|--------|-------|
| Python 3.13.5 | ✅ Installato | Ottimo |
| git, curl, wget | ✅ Installati | OK |
| python3-pip, setuptools, wheel | ✅ Installati | OK |
| python3-smbus | ✅ Installato | I2C funz |
| espeak-ng | ✅ Installato | TTS pronto |
| libopenblas-dev | ✅ Installato | NumPy/scipy OK |
| python3-picamera2 | ✅ Installato | Camera pronta |
| **python3-gpiod** | ⚠️ Non disponibile su trixie | Non critico |
| **libgpiod2** | ⚠️ Non disponibile su trixie | Non critico |
| **libatlas-base-dev** | ⏭️ Saltato (obsoleto) | Sostituito da libopenblas |
| i2c-tools | ✅ Installato | I2C debug OK |

---

## 🔍 Come Verificare il Sistema

```bash
# Verifich sensori
i2cdetect -y 1

# Verificare audio
aplay -l

# Verificare ollama
ollama list

# Verificare importazioni Python
source /home/spooky/spooky/robot-core/venv/bin/activate
python -c "import cv2, robot_hat, ollama; print('✓ Tutte le dipendenze critiche disponibili')"
```

---

## 📝 Prossimi Step

1. **Reboot** (se non già fatto)
   ```bash
   sudo reboot
   ```

2. **Verifica I2C dopo reboot**
   ```bash
   i2cdetect -y 1
   ```

3. **Avvia Spooky**
   ```bash
   bash /home/spooky/spooky/robot-core/scripts/start.sh
   ```

4. **Monitora log**
   ```bash
   tail -f /home/spooky/spooky/robot-core/logs/spooky.log
   ```

---

## 💡 Note Tecniche

### Perché trixie invece di Bookworm?

Debian trixie è la versione "testing" (development) di Debian. Ha pacchetti più recenti, ma alcuni potrebbero mancare temporaneamente.

Se volete stabilità, RPi OS Bookworm è il target ufficiale. Ma trixie funziona comunque.

### libgpiod vs robot-hat GPIO

- **libgpiod**: Generic GPIO library di Linux
- **robot-hat**: SunFounder's specific GPIO wrapper (include la sua implementazione)

Robot-hat ha il suo GPIO integrato, quindi libgpiod/python3-gpiod non sono strettamente necessari.

### libatlas-base-dev → libopenblas-dev

- **libatlas-base-dev**: Linear algebra — obsoleto su Debian trixie
- **libopenblas-dev**: OpenBLAS — il moderno sostituto

NumPy/scipy useranno automaticamente libopenblas. ✓

---

## ❓ Domande Frequenti

**D: Lo script fallirà se lancio di nuovo?**
A: No! I warnings saranno gli stessi, ma non bloccano. Tutti i package critici sono già installati.

**D: Spooky funzionerà senza python3-gpiod?**
A: Sì! robot-hat ha il suo GPIO. python3-gpiod è solo fallback opzionale.

**D: E se volessi Bookworm invece di trixie?**
A: Reinstalla RPi OS ufficiale da SD card. Ma trixie funziona comunque!

**D: Come faccio a sapere se tutto funziona?**
A: Aspetta il reboot, poi lancia `bash start.sh`. Se Spooky gira, tutto è OK.

---

## 📞 Support

Se persistono problemi:

1.Condividi l'output completo di: `bash install_rpi.sh 2>&1 | tee install_complete.log`
2. Verifica che ollama sia lanciatoò
3. Controllaò RAM libera: `free -h`
4. Checkå CPU temp: `vcgencmd measure_temp`

