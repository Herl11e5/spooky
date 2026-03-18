# 🕷️ Spooky — Robot Ragno da Scrivania

Robot companion locale basato su **SunFounder PiCrawler + Raspberry Pi 5 (8 GB)**.

Architettura modulare, completamente locale (nessun cloud), con:
- Riconoscimento volti locale
- Memoria persistente (SQLite)
- Ragionamento LLM locale (ollama)
- Modalità sorveglianza notturna
- Dashboard web
- Apprendimento adattivo

---

## Hardware richiesto

| Componente | Dettaglio |
|---|---|
| Raspberry Pi 5 | 8 GB RAM |
| SunFounder PiCrawler | Kit completo (servos, chassis) |
| Camera | Pi Camera Module 3 o compatibile |
| Microfono | USB o I2S |
| Altoparlante | USB/I2S (es. HiFiBerry) |
| Alimentazione | Ufficiale 27W |

---

## Installazione rapida (RPi)

```bash
# 1. Clona il repo
git clone https://github.com/TUO_UTENTE/spooky.git ~/spooky
cd ~/spooky

# 2. Esegui lo script di installazione automatica
bash robot-core/scripts/install_rpi.sh

# 3. (Opzionale) Configura parametri locali
cp robot-core/config/local.yaml.example robot-core/config/local.yaml
nano robot-core/config/local.yaml

# 4. Avvia
bash robot-core/scripts/start.sh

# 5. Apri la dashboard
# http://<ip-del-raspberry>:5000
```

Per l'avvio automatico al boot con systemd → vedi `robot-core/scripts/install_rpi.sh` (lo fa in automatico).

---

## Struttura del progetto

```
spooky/
├── robot-core/              # Sistema modulare (NUOVO)
│   ├── main.py              # Entry point
│   ├── config/
│   │   ├── robot.yaml       # Configurazione principale
│   │   └── local.yaml       # Override locale (gitignored)
│   ├── core/
│   │   ├── bus.py           # Event bus pub/sub
│   │   ├── modes.py         # State machine delle modalità
│   │   ├── safety.py        # Monitor sicurezza
│   │   └── config.py        # Loader YAML
│   ├── services/
│   │   ├── motor.py         # Astrazione PiCrawler
│   │   ├── sensor.py        # Ultrasuoni, temp, RAM
│   │   ├── vision.py        # Camera, face detect/riconosc.
│   │   ├── audio.py         # TTS (espeak), STT (Vosk)
│   │   ├── mind.py          # LLM brain (ollama)
│   │   ├── memory.py        # SQLite: episodic/semantic/social
│   │   ├── conscience.py    # Drive interni (energia, curiosità…)
│   │   ├── choreography.py  # Sequenze di animazione
│   │   ├── night_watch.py   # Modalità sorveglianza
│   │   ├── alert.py         # Fusione eventi + escalation
│   │   ├── alert_adapters.py# Telegram / Webhook / HA
│   │   ├── learning.py      # Estrazione preferenze
│   │   ├── experiment.py    # Micro-esperimenti adattativi
│   │   ├── summarizer.py    # Riassunto giornaliero memoria
│   │   └── dashboard.py     # Flask web UI
│   ├── skills/
│   │   ├── track_face.py    # Tracking volto con la testa
│   │   ├── idle_behavior.py # Comportamenti idle
│   │   └── patrol.py        # Pattuglia scrivania
│   └── scripts/
│       ├── install_rpi.sh   # Setup completo RPi
│       ├── start.sh         # Avvio manuale
│       ├── enroll_face.py   # Registrazione volti CLI
│       └── spooky.service   # Unit systemd
├── spooky.py                # Monolite originale (deprecato)
└── avvio.sh                 # Script avvio originale (deprecato)
```

---

## Modalità

| Modalità | Comportamento |
|---|---|
| `companion_day` | Socievole, traccia volti, risponde a comandi vocali |
| `focus_assistant` | Silenzioso, solo wake-word |
| `idle_observer` | Osservazione passiva, comportamenti ambient |
| `night_watch` | Sorveglianza silenziosa, alert livellati L0–L3 |
| `safe_shutdown` | Fermo totale, solo su fault critico |

Cambio modalità via voce: *"Spooky, modalità notte"* / *"Spooky, modalità giorno"*

---

## Comandi vocali

Wake word: **"Spooky"** (configurabile)

| Comando | Azione |
|---|---|
| "Spooky, modalità notte" | Entra in night_watch |
| "Spooky, modalità giorno" | Torna a companion_day |
| "Spooky, silenzio / focus" | Modalità focus_assistant |
| "Spooky, cosa ricordi?" | Legge la memoria recente |
| "Spooky, mi chiamo Marco" | Registra il nome utente |
| "Spooky, cosa vedi?" | Descrizione scena (ollama vision) |
| Qualsiasi domanda | Risposta LLM (llama3.2:1b) |

---

## Dashboard

Accessibile su `http://<ip-rpi>:5000`

Endpoint REST:

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/api/state` | Stato completo robot |
| GET | `/api/persons` | Persone note |
| GET | `/api/memory` | Episodi + fatti semantici |
| GET | `/api/alerts` | Alert night watch recenti |
| GET | `/api/night_log` | Log notturno dettagliato |
| GET | `/api/experiments` | Esperimenti correnti/storico |
| POST | `/api/command` | Inietta comando testo |
| POST | `/api/mode` | Cambia modalità |
| POST | `/api/summarize` | Avvia riassunto manuale |
| GET | `/stream` | SSE live events |

---

## Configurazione Telegram (opzionale)

```yaml
# robot-core/config/local.yaml
alerts:
  telegram:
    enabled: true
    token: "123456789:ABCdef..."
    chat_id: "123456789"
```

---

## Registrazione volti

```bash
# CLI interattivo — guarda la camera per 15 secondi
cd ~/spooky/robot-core
python scripts/enroll_face.py --name "Marco" --id "marco"

# Elimina persona
python scripts/enroll_face.py --delete --id "marco"

# Lista persone registrate
python scripts/enroll_face.py --list
```

---

## Modelli raccomandati

| Tipo | Modello | RAM | Comando |
|---|---|---|---|
| Testo (richiesto) | llama3.2:1b | ~900 MB | `ollama pull llama3.2:1b` |
| Testo (migliore) | llama3.2:3b | ~2 GB | `ollama pull llama3.2:3b` |
| Vision (opzionale) | moondream | ~1.7 GB | `ollama pull moondream` |
| STT | vosk-model-small-it-0.22 | ~80 MB | auto-download |

---

## Note sicurezza

- Tutti i dati biometrici (volti) restano sul dispositivo
- Nessuna registrazione audio continua
- Snapshot notturni solo su evento, non in continuo
- `save_jpeg: false` di default (solo metadati JSON)
- Safety monitor hardware: stop automatico su ostacolo, surriscaldamento, errori attuatori
