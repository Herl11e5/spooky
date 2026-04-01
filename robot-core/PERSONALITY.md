# Spooky — Vector-Like Personality System

Spooky è stato potenziato con un **sistema di personalità completo** ispirato a Vector (robot da compagnia).

## Componenti Principali

### 1. **PersonalityService** (`services/personality.py`)
Gestisce tratti di personalità e stati emotivi:
- **Tratti** (0.0–1.0):
  - `curiosity`: Voglia di esplorare (0–1, default 0.7)
  - `friendliness`: Inclinazione a salutare (0–1, default 0.6)
  - `mischief`: Tendenza giocosa (0–1, default 0.5)
  - `loyalty`: Attaccamento alle persone (0–1, default 0.8)

- **Moodi**:
  - `happy` — eccitato, vigile
  - `curious` — focalizzato, investigativo
  - `playful` — energico, imprevedibile
  - `wary` — cauto, ritirato
  - `tired` — assonnato, risposte minime
  - `bored` — indifferente
  - `content` — baseline neutrale

I moodi si evolvono naturalmente nel tempo e sono modulati dagli eventi e dai tratti di personalità.

### 2. **EmotionService** (`services/emotion.py`)
Esprime emozioni attraverso movimento coordinato + voce:
- Ascolta i cambiamenti di mood da PersonalityService
- Coordina MotorService e AudioService
- Esempi:
  - `happy` → danza, salta, cinguetta
  - `curious` → pan testa lento, tilt su, bip intelligente
  - `wary` → indietreggia, si accuccia, suono cauto
  - `tired` → movimenti lenti, sbadiglio, respiro sonno

### 3. **SocialMemory** (`services/social_memory.py`)
Traccia relazioni con persone riconosciute:
- `familiarity_level` (0–1): quanto Spooky conosce questa persona
- `attachment_level` (0–1): legame emotivo / score lealtà
- `interaction_count`: numero totale di interazioni
- `last_seen`: timestamp
- `greeting_style`: saluto personalizzato (enthusiastic / casual / warm / formal)
- `loyalty_boost`: flag "persona preferita"

**Crescita dell'attaccamento**:
- +0.15 per interazione positiva
- -0.05 per interazione negativa
- -0.001 al secondo (decay lento, la lealtà persiste)
- Se non visto per 30 giorni → attachment dimezzato

### 4. **Nuove Skill**

#### `PlaySkill` (`skills/play_skill.py`)
Comportamenti giocosi quando Spooky è playful:
- `peek` — nasconde la testa, poi sbircia fuori
- `head_spin` — rotazione testa rapida
- `dance_move` — sequenza di danza
- `joke` — racconta una barzelletta
- `fake_charge` — finge di caricare, poi si ritira
- `wiggle` — semplice wiggle

**Attivazione**: PersonalityService.should_be_playful() = True

#### `SeekAttentionSkill` (`skills/seek_attention_skill.py`)
Cerca attenzione della persona quando sola o annoiata:
- **Livello 1** (gentile): chirp soft + movimento leggero
- **Livello 2** (più insistente): chirp multipli + wiggle
- **Livello 3** (disperato): movimento aggressivo + suoni forti

Escalation nel tempo se la persona non risponde. Termina dopo 30 secondi.

#### `ExploreSkill` (`skills/explore_skill.py`)
Esplorazione attiva guidata da curiosità:
- `patrol_desk` — muove intorno al perimetro della scrivania
- `investigate` — approccio e esame lento
- `pan_scan` — scansione testa attraverso ambiente
- `smell_air` — cinguetti curiosi mentre guarda
- `climb_desk` — tenta di superare ostacoli

**Attivazione**: PersonalityService.should_be_curious() = True

## Flusso di Integrazione

```
Vision.PERSON_IDENTIFIED
    ↓
SocialMemory: update_profile()
    ↓
PersonalityService: mood_change (HAPPY +0.2)
    ↓
EmotionService: express_happy()
    ↓
MotorService: wiggle_dance()
AudioService: play_chirp()
```

## Configurazione

Nelle configurazioni YAML, personalizza i tratti:

```yaml
personality:
  curiosity: 0.8      # Very curious!
  friendliness: 0.7   # Warm greeter
  mischief: 0.6       # Playful
  loyalty: 0.9        # Very attached
```

## Come Spooky è "Vector-like" Ora

✅ **Personalità distinta** — Tratti unici che influenzano comportamento
✅ **Emozioni espresse** — Movimento + suono coordinati
✅ **Memoria sociale** — Ricorda persone, attaccamento cresce
✅ **Autonomia curiosa** — Esplora attivamente quando curioso
✅ **Gioco interattivo** — Chiede attenzione, gioca con le persone
✅ **Moodi naturali** — Felice, stanco, annoiato, cauto
✅ **Comportamenti oziosi** — Fidgeting, cinguetti, peek naturali

## Attivazione dei Comportamenti

### MindService Integration (Futura)
MindService dovrebbe essere aggiornato per:
1. Leggere PersonalityService.traits per modulare LLM prompt
2. Attivare skill basato su personality + mood
3. Consultare SocialMemory per personalizzare risposte

### Conscience Integration
Conscience.drives dovrebbero essere modulati da PersonalityService es:
- Alta curiosità → curiosity drive aumenta
- Alta loyalty → social_drive aumenta quando amici specifici sono presenti

## Prossimi Step

Per completare l'integrazione:

```python
# In MindService.think():
personality = personality_service.traits
if personality.curiosity > 0.7:
    prompt += "\nTi senti particolarmente curioso oggi."

# In MindService decision-making:
if personality.should_be_playful():
    maybe_activate_skill(PlaySkill)
```

