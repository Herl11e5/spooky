# Spooky — Robot Companion da Scrivania

Robot autonomo basato su **SunFounder PiCrawler + Raspberry Pi 5 (8 GB)**.
Locale: nessun cloud, nessuna telemetria. I modelli linguistici girano su un computer della
stessa rete (Ollama), i dati restano sul robot.

---

## Hardware richiesto

| Componente | Dettaglio |
|---|---|
| Raspberry Pi 5 | 8 GB RAM (consigliato) |
| SunFounder PiCrawler | Kit completo (chassis, 12 servos, scheda robot-hat) |
| Camera | Pi Camera Module 3 o compatibile libcamera |
| Microfono | USB o I2S |
| Altoparlante | USB o I2S (es. HiFiBerry MiniAmp) |
| Alimentazione | Ufficiale RPi 27W USB-C |

---

## Spooky come cervello del PiCrawler (ramo `picrawler-bridge`)

Spooky può fare da **cervello** del corpo [robot-emulator](../robot-emulator): il corpo
(`picrawler.service`) è l'unico processo che muove i servo, legge camera, sonar e IMU e
applica la sicurezza; Spooky gli manda *intenzioni* via HTTP (`services/picrawler_client.py`)
con sorgente `brain`, la priorità più bassa: chi usa la dashboard o un gamepad ha sempre
la precedenza. È la divisione `robotd`/client di microduck.

- **Movimento**: `PicrawlerMotorService` traduce i comandi delle skill in passi e gesti del
  corpo (saluta, annuisci, balla…); gli sguardi diventano inclinazioni del corpo, senza girarsi.
  Il corpo lo lascia muovere solo con «Può muoversi da solo» acceso nella dashboard (spento a
  ogni avvio), entro 15 cm da dove lo si è lasciato, e mai a batteria scarica.
- **Volti ed espressioni**: li trova il corpo, accanto alla camera (YuNet, SFace, MobileFaceNet)
  e ne pubblica posizione, *embedding* d'identità ed espressione; Spooky riconosce le persone
  confrontando gli embedding (`EmbeddingRecognizer`) e reagisce alle espressioni
  (`skills/empathy_skill.py`). Nessun fotogramma viene scaricato per i volti.
- **Memoria** (`services/knowledge.py`): ciò che sente intorno (solo testo), vede e dice diventa
  osservazione; prima di ogni risposta richiama i ricordi più vicini per significato
  (embeddinggemma); ogni notte li distilla in fatti duraturi (gemma3) e cancella il testo grezzo.
  Un fatto sentito una volta sola resta un'ipotesi («[maybe]») finché non lo risente.
- **Voce**: risponde in inglese con la propria lingua droide (`services/droid_voice.py`);
  capisce l'italiano (Vosk). Il nome lo cerca un secondo riconoscitore che conosce solo
  «spooky» (`WakeSpotter`): il modello italiano da solo non lo scrive mai. Mentre il corpo si
  muove serve più certezza, perché i motori si sentono.
- **Pisolino** (`skills/nap_skill.py`): cinque minuti senza nessuno e si siede (servo spenti);
  un volto noto o il suo nome lo svegliano. Il corpo si siede comunque da solo dopo 15 minuti.
- **Sussulti** (`skills/startle_skill.py`): un rumore secco (un battito di mani, qualcosa che
  cade — non una voce, che cresce invece di esplodere), qualcosa che gli arriva addosso in
  fretta, la luce che si spegne o si accende: sobbalza (gesto `flinch`) ed esclama; se è solo,
  si guarda intorno. Poi ci si abitua: la seconda volta solo un verso, la terza niente, finché
  non passano due minuti tranquilli. Mai mentre parla o gli si parla, né in modalità
  concentrazione; di notte resta immobile e lascia l'evento agli allarmi.
- **Salute**: `/api/state` dice di ogni parte se funziona (microfono, riconoscimento vocale,
  voce, collegamento al corpo). Il modello linguistico è a parte: il Mac può dormire, e non è
  mai colpa di una release.
- **Sul Pi**: `spooky-brain.service` (`deploy/`), configurazione `config/picrawler-pi.yaml`;
  si installa con `python scripts/release.py spooky` dal repository del corpo, che verifica la
  versione sul robot e torna alla precedente se non parte o se smette di funzionare una parte
  che prima funzionava.

Tutto si gestisce dalla dashboard del corpo (`http://<robot>:8000`, pannello «Cervello»).

---

## Installazione (RPi)

```bash
# 1. Clona il repo
git clone https://github.com/Herl11e5/spooky.git ~/spooky

# 2. Setup completo automatico
#    (Python 3.11, dipendenze, ollama, vosk, systemd service)
bash ~/spooky/robot-core/scripts/install_rpi.sh

# 3. Configura parametri locali (Telegram, HA, override LLM, ecc.)
nano ~/spooky/robot-core/config/local.yaml

# 4a. Avvio manuale (per test)
bash ~/spooky/robot-core/scripts/start.sh

# 4b. Avvio automatico al boot
sudo systemctl start spooky
sudo journalctl -u spooky -f

# Dashboard → http://<ip-rpi>:5000
```

`install_rpi.sh` scarica automaticamente: vosk-model-small-it-0.22 (~50 MB) e llama3.2:1b (~700 MB).

---

## Architettura

Ogni componente comunica esclusivamente tramite un **event bus pub/sub** — nessun import diretto fra servizi.

```
spooky/
├── robot-core/
│   ├── main.py                  # RobotRuntime: boot ordinato di tutti i servizi
│   ├── core/
│   │   ├── bus.py               # EventBus thread-safe (pub/sub asincrono)
│   │   ├── modes.py             # State machine: 5 modalità + tabella transizioni
│   │   ├── safety.py            # Watchdog hardware (ostacoli, temp, RAM, servos)
│   │   └── config.py            # Loader YAML con deep merge (robot.yaml + local.yaml)
│   ├── services/
│   │   ├── motor.py             # Astrazione PiCrawler (sim fallback automatico)
│   │   ├── sensor.py            # Ultrasuoni, temperatura CPU, RAM libera
│   │   ├── vision.py            # Camera → face detect (Haar) → LBPH recognition
│   │   ├── audio.py             # STT: Vosk italiano | TTS: espeak-ng (thread-safe)
│   │   ├── mind.py              # OllamaBrain: selezione modello, lock globale, history
│   │   ├── memory.py            # SQLite WAL: episodic / semantic / social / procedural
│   │   ├── conscience.py        # Drive interni: energia, social, curiosità, attenzione
│   │   ├── choreography.py      # 13 sequenze di animazione (excited, alert, shy…)
│   │   ├── night_watch.py       # Sorveglianza: fusione eventi L0–L3, patrol, snapshot
│   │   ├── alert.py             # Motore escalation alert con cooldown e burst detection
│   │   ├── alert_adapters.py    # Telegram / Webhook / Home Assistant
│   │   ├── learning.py          # Estrazione preferenze (regex IT) + tuning parametri
│   │   ├── experiment.py        # Micro-esperimenti adattativi (BASELINE→ADOPTED)
│   │   ├── summarizer.py        # Compressione episodic→semantic alle 03:00
│   │   └── dashboard.py         # Flask: SSE live stream + REST API
│   ├── skills/
│   │   ├── track_face.py        # Inseguimento volto con controllo proporzionale
│   │   ├── idle_behavior.py     # Comportamenti ambient (look_around, micro_move…)
│   │   └── patrol.py            # Pattuglia scrivania con gate ostacoli
│   ├── config/
│   │   ├── robot.yaml           # Configurazione principale
│   │   └── local.yaml.example   # Template override locale (gitignored)
│   ├── scripts/
│   │   ├── install_rpi.sh       # Setup completo RPi 5 (Bookworm)
│   │   ├── start.sh             # Avvio manuale con venv + ollama check
│   │   ├── enroll_face.py       # CLI registrazione volti
│   │   └── spooky.service       # Unit systemd (MemoryMax=6G)
│   └── tests/
│       ├── test_bus.py          # EventBus + Event API
│       ├── test_modes.py        # State machine + transizioni
│       ├── test_memory.py       # SQLite: episodic, facts, persons, params
│       └── test_learning.py     # Estrazione regex + tune_parameter bounds
```

---

## Modalità

| Modalità | Comportamento |
|---|---|
| `companion_day` | Socievole — traccia volti, risponde a comandi vocali, usa LLM |
| `focus_assistant` | Silenzioso — solo wake-word, nessuna iniziativa |
| `idle_observer` | Passivo — comportamenti ambient, nessun LLM attivo |
| `night_watch` | Sorveglianza — alert livellati L0–L3, patrol 360°, snapshot su evento |
| `safe_shutdown` | Fermo totale — solo su fault critico (overtemp, OOM, errori servo) |

Transizioni valide:

```
companion_day ←→ focus_assistant
companion_day ←→ idle_observer
companion_day ←→ night_watch
qualsiasi → safe_shutdown  (solo il safety monitor)
```

---

## Comandi vocali

Wake word: **"Spooky"** (configurabile in `robot.yaml`)

| Comando | Azione |
|---|---|
| "Spooky, modalità notte" | → `night_watch` |
| "Spooky, modalità giorno" | → `companion_day` |
| "Spooky, focus / silenzio" | → `focus_assistant` |
| "Spooky, cosa vedi?" | Descrizione scena via LLM vision (moondream) |
| "Spooky, cosa ricordi?" | Legge episodi + fatti semantici recenti |
| "Spooky, mi chiamo Marco" | Estrae e salva il nome in memoria semantica |
| "Spooky, risposte brevi" | Tuning automatico `max_tokens` in memoria procedurale |
| "Spooky, parla più lentamente" | Tuning `voice_speed` |
| Qualsiasi domanda | Risposta LLM (llama3.2) con contesto memoria |

---

## Memoria

Quattro livelli su SQLite (WAL mode, scritture async):

| Tabella | Contenuto |
|---|---|
| `episodic` | Ogni interazione con timestamp, chi, cosa, azione, esito |
| `semantic_facts` | Fatti stabili (nome utente, preferenze) — promossi dopo 3 ripetizioni |
| `social_profiles` | Profilo per persona: familiarità, stile saluto, orari abituali |
| `procedural` | Parametri skill tuned (gain tracking, max_tokens, temperature…) |

Il **summarizer** comprime ogni notte (03:00) gli episodi recenti in fatti semantici.

---

## Night Watch

Livelli di escalation:

| Livello | Trigger | Azione |
|---|---|---|
| L0 | Movimento solo | Log silenzioso |
| L1 | Movimento + suono insolito | Coreografia alert |
| L2 | Persona sconosciuta rilevata | Alert + snapshot JSON |
| L3 | Persona sconosciuta + disturbo | Alert + snapshot + notifica esterna |

- Patrol automatico ogni N minuti (rotazione 360°)
- Snapshot: solo metadati JSON di default — JPEG opzionale (`save_jpeg: true`)
- Cooldown per burst detection (evita spam alert)

---

## Dashboard

`http://<ip-rpi>:5000`

| Metodo | Path | Descrizione |
|---|---|---|
| GET | `/api/state` | Stato completo (modalità, drives, sensori, ultimo volto) |
| GET | `/api/persons` | Persone registrate nel face DB |
| GET | `/api/memory` | Episodi recenti + fatti semantici |
| GET | `/api/alerts` | Alert night watch recenti |
| GET | `/api/night_log` | Log notturno dettagliato |
| GET | `/api/experiments` | Esperimenti correnti e storici |
| GET | `/api/learning` | Fatti estratti e parametri tuned |
| POST | `/api/command` | Inietta comando testo (body: `{"text": "..."}`) |
| POST | `/api/mode` | Cambia modalità (body: `{"mode": "night_watch"}`) |
| POST | `/api/summarize` | Avvia compressione memoria manuale |
| GET | `/stream` | SSE — eventi live in tempo reale |

---

## Registrazione volti

```bash
cd ~/spooky/robot-core

# Registra — cattura 15 campioni dalla camera
python scripts/enroll_face.py --name "Marco" --id "marco"

# Lista persone registrate
python scripts/enroll_face.py --list

# Dettagli persona
python scripts/enroll_face.py --info --id "marco"

# Elimina persona
python scripts/enroll_face.py --delete --id "marco"
```

Il riconoscimento usa **OpenCV LBPH** — zero dipendenze extra, ~1 MB RAM, funziona offline.

---

## Notifiche esterne (opzionale)

```yaml
# robot-core/config/local.yaml

# Telegram
alerts:
  telegram:
    enabled: true
    token: "123456789:ABCdef..."
    chat_id: "123456789"

# Home Assistant
alerts:
  home_assistant:
    enabled: true
    url: "http://homeassistant.local:8123"
    token: "il-tuo-long-lived-token"

# Webhook generico
alerts:
  webhook:
    enabled: true
    url: "http://192.168.1.10:8080/spooky/alert"
```

---

## Modelli LLM

| Tipo | Modello | RAM usata | Comando |
|---|---|---|---|
| Testo (default) | llama3.2:1b | ~900 MB | `ollama pull llama3.2:1b` |
| Testo (migliore) | llama3.2:3b | ~2 GB | `ollama pull llama3.2:3b` |
| Vision | moondream | ~1.7 GB | `ollama pull moondream` |
| Vision (alt) | llava:7b | ~5 GB | `ollama pull llava:7b` |

I modelli vision vengono scaricati dalla RAM subito dopo ogni inferenza (`keep_alive=0`).
Il modello testo rimane in RAM 120s durante la finestra di conversazione.
Un lock globale impedisce il caricamento concorrente di più modelli (prevenzione OOM).

---

## Test

```bash
cd ~/spooky/robot-core
pip install pytest
python -m pytest tests/ -v
# 32 passed
```

---

## Note privacy e sicurezza

- Tutti i dati biometrici (volti, embeddings) restano sul dispositivo — mai trasmessi
- L'audio non viene mai registrato. Con «Ascolta e ricorda tutto» acceso (dashboard → Memoria)
  Spooky tiene il **testo** di ciò che sente intorno, anche di chi non gli parla: ogni notte ne
  ricava fatti e cancella il testo dopo 48 ore. Va spento dove ci sono persone che non lo sanno
  (in Italia tenere conversazioni altrui senza consenso può essere reato, e il controllo a
  distanza dei lavoratori è regolato). Spento, ricorda solo ciò che gli viene detto
- «Dimentica» su una persona cancella volto, ricordi e fatti imparati su di lei
- Snapshot notturni: solo metadati JSON di default (`save_jpeg: false`)
- Safety monitor hardware: stop automatico su ostacolo < 15 cm, CPU > 80°C, RAM < 300 MB
- `config/local.yaml` è in `.gitignore` — token e credenziali non vengono mai committati
