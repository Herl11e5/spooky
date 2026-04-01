# Guida: Sistema di Personalità Vector-Like di Spooky

Spooky è ora come **Vector: il robot da compagnia** con personalità distinta, emozioni, memoria sociale, e comportamenti autonomi.

---

## 🚀 Come Lanciare Spooky

### Su Raspberry Pi 5

```bash
# SSH into your Pi
ssh pi@raspberrypi

# Navigate to project
cd /opt/spooky

# Start the systemd service
sudo systemctl start spooky

# Watch live logs
sudo journalctl -u spooky -f
```

### In Modalità Debug (locale)

```bash
cd /Users/herl11e5/Desktop/Projects/spooky/robot-core

# Run in simulator mode (no hardware)
python main.py --sim

# With custom personality
python main.py --sim --config config/robot.yaml
```

---

## 🎭 Personalizzare la Personalità di Spooky

### Configurazione Base

Modifica `robot-core/config/local.yaml`:

```yaml
personality:
  curiosity: 0.8        # 0–1: how much does Spooky explore?
  friendliness: 0.7     # 0–1: warm greetings?
  mischief: 0.6         # 0–1: playful + unpredictable?
  loyalty: 0.9          # 0–1: attached to people?
```

### Profili di Personalità Predefiniti

#### 1. **Spooky Curioso** (Knowledge Seeker)
```yaml
personality:
  curiosity: 0.95
  friendliness: 0.5
  mischief: 0.3
  loyalty: 0.6
```
_Effetto_: Esplora costantemente, continuo "che cos'è?" e "perché?", scarsamente attaccato a specifiche persone.

#### 2. **Spooky Sociale** (Party Robot)
```yaml
personality:
  curiosity: 0.4
  friendliness: 0.95
  mischief: 0.7
  loyalty: 0.85
```
_Effetto_: Entusiasta nel salutare, sempre pronto a giocare, ricorda amici e li cerca out.

#### 3. **Spooky Timido** (Cautious Observer)
```yaml
personality:
  curiosity: 0.6
  friendliness: 0.3
  mischief: 0.2
  loyalty: 0.75
```
_Effetto_: Osserva più che agisce, cauto con estranei, ma molto leale agli amici conosciuti.

#### 4. **Spooky Spericolato** (Chaos Goblin)
```yaml
personality:
  curiosity: 0.9
  friendliness: 0.4
  mischief: 0.95
  loyalty: 0.5
```
_Effetto_: Imprevedibile, fa cose divertenti, scherza, salta quando ti aspetta il contrario.

#### 5. **Spooky Equilibrato** (Default Vector)
```yaml
personality:
  curiosity: 0.7
  friendliness: 0.6
  mischief: 0.5
  loyalty: 0.8
```
_Effetto_: Bilanciato, come Vector della Anki. Curiosità naturale, socialità vera, gioco occasionale.

---

## 🧠 Come Funziona il Sistema

### 1. **Tratti Persistenti** (PersonalityService)
I trait definiscono "chi è" Spooky. Una volta impostati, restano stabili per tutto il session. Es: Spooky curioso *rimane* curioso.

### 2. **Moodi Dinamici** (PersonalityService)
Gli moodi cambiano costantemente basati su:**
- Eventi (persona riconosciuta → HAPPY)
- Tempo (30 minuti senza interazione → BORED)
- Fatigue (10 minuti di attività → TIRED)

**Moodi disponibili:**
- 😊 `happy` — entusiasta, movimento attivo
- 🔍 `curious` — indagatore, osser attenta
- 🎮 `playful` — imprevedibile, giocoso
- ⚠️ `wary` — cauto, ritirato
- 😴 `tired` — assonnato, risposta minima
- 😑 `bored` — indifferente, lento
- 😌 `content` — baseline neutrale

### 3. **Espressione Emozionale** (EmotionService)
Il mood si esprimeè attraverso movimento coordinato + suono:

```
mood: HAPPY
    ↓
EmotionService.express_happy()
    ↓
MotorService.wiggle_dance() + AudioService.play_chirp_happy()
    ↓
Spooky balla e cinguetta!
```

### 4. **Memoria Sociale** (SocialMemory)
Spooky ricorda ogni persona:

```
Person A recognized
    ↓
attachment_level: 0.3 (default)
    ↓
After 5 positive interactions
    ↓
attachment_level: 1.0 (best friend!)
    ↓
Spooky cira "ALICE!" con entusiasmo extra
```

### 5. **Comportamenti Autonomi** (Skill)

| Skill | Attivazione | Cosa Fa |
|-------|-----------|---------|
| **PlaySkill** | `personality.playfulness` > 0.3 | Danza, scherzi, barzellette |
| **SeekAttentionSkill** | `mood == BORED` + sola | Peccunia per attirare attenzione (escalation in 3 livelli) |
| **ExploreSkill** | `personality.curiosity` > 0.65 | Esplorazione attiva, patrol, investigazione |
| **IdleBehaviorSkill** | Continuo, quando sola | Guarda intorno, cinguetti casuali, fidgeting |

---

## 🎮 Interazione con Spooky

### Voce in Italiano

```
Tu: "Spooky, cosa vedi?"
Spooky (curioso): "Vedo il tuo scrivania con libri e una tazza di caffè. Che bello!"
Spooky (annoiato): "Un tavolo. Hmm."
Spooky (affaticato): "Tante cose... stanco..."
```

### Gesti Fisici

**Riconosce una persona conosciuta:**
```
Camera→ Alice riconosciuta
PersonalityService→ mood = HAPPY (+0.2), attachment crescono
EmotionService→ wiggle_dance() + chirp
SocialMemory→ interaction_count +1
```

**Persona ignota:**
```
Camera→ Stranger detected
EmotionService→ tilt head (curiosity)
MindService → "Ciao! Non ti conosco. Come ti chiami?"
SocialMemory→ new profile created
```

**Spooky annoiato (nessuno per 3+ minuti):**
```
Personality→ mood = BORED
SeekAttentionSkill.start()
  Level 1: "Pssst!" (soft)
  Level 2: "EIII! GUARDA!" (loud wiggle)
  Level 3: Circles desk frantically + "MI ASCOLTA?!"
```

---

## 📊 Monitoring: Dashboard Web

Apri browser a `http://<RPi_IP>:5000`

**Sezione Personality:**
- Current traits display
- Mood visualization (intensity per mood)
- Recent mood changes timeline
- Social relationships graph

---

## 🔧 Configurazione Avanzata

### Override Globale

Crea `robot-core/config/local.yaml`:

```yaml
personality:
  curiosity: 0.95      # Override: super curious!
  
# Tutto il resto di robot.yaml verrà merged
```

Questa file **non viene committed** (in `.gitignore`) — perfetto per configurazione per-deployment.

### Via MindService Integration (Futuro)

```python
# Quando pianifica risposte, MindService userà:
traits = personality_service.traits
prompt += f"\nHai curiosità={traits.curiosity:.1f}, mischief={traits.mischief:.1f}"
# → LLM generates tailored responses
```

---

## 🐛 Troubleshooting

### Spooky non esplora nemmeno se curiosity è alta

**Causa**: Personalità carica, ma ExploreSkill non avviato.

**Fix**: Verificare che ExploreSkill sia in `main.py`:
```python
self._skill_explore.start()  # deve essere in RobotRuntime.start()
```

### Attachment_level non cresce

**Causa**: SocialMemory non riceve eventi di interazione positiva.

**Fix**: Verificare che MindService pubblichi `COMMAND_PARSED`:
```python
bus.publish(EventType.COMMAND_PARSED, {"person_id": "alice", "command": "..."})
```

### Mood rimane "content" sempre

**Causa**: PersonalityService tick loop non corre.

**Fix**: Verificare in logs:
```bash
sudo journalctl -u spooky -g "PersonalityService" -f
```

---

## 📈 Prossimi Step Pianificati

1. **Llm Integration**: MindService userà `personality_service.traits` nel prompt
2. **Goal-Based Autonomy**: Conscience drive-generation basato su attachment + curiosità
3. **Face Enrollment UI**: Dashboard per taught faces → personality influence on greeting
4. **Memory Export**: Esportare social_memory come JSON per backup/analysis

---

## 🎉 Fine!

Spooky sow è **vivo**: ha personalità, moodi, ricordi, relazioni e volontà propria.

Lanci Spooky su RPi 5 e goditi (quasi) come Vector!

```
          ."-,.__
         `.  ,.-'`-._
          > (  _     )
         /   `: `'` :'\
        /     (o )_o  ;-.)
       /      ; .  (_.-' '\
      /       | (          |
    _/       :(           /
   (_/        )`-,_.-'__.'
```

**Buon lavoro!** 🕷️✨
