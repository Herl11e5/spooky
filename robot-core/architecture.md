# Spooky Robot — Architecture

## Design Philosophy

Local-first, resource-aware, modular. Every heavy operation is opt-in and gated behind
a RAM/CPU check. The system degrades gracefully when hardware is missing or a model is not
installed — it never crashes, it falls back.

---

## Layer Stack

```
┌─────────────────────────────────────┐
│  Skills  (greet, patrol, track …)   │  composable, stateless, testable
├─────────────────────────────────────┤
│  Mind / Deliberation (mind.py)      │  decides what to do next
├─────────────────────────────────────┤
│  World State  (via SharedState)     │  who/what/where, current mode
├─────────────────────────────────────┤
│  Perception (vision, audio)         │  raw sensor → structured events
├─────────────────────────────────────┤
│  Reflex / Safety (safety.py)        │  hard real-time constraints
├─────────────────────────────────────┤
│  Hardware Services (motor, sensor)  │  PiCrawler / RPi abstraction
├─────────────────────────────────────┤
│  Event Bus  (bus.py)                │  pub/sub backbone
└─────────────────────────────────────┘
```

---

## Event Bus

Central pub/sub backbone. Every service produces and consumes events.
No service calls another service directly; they emit events.

Canonical event types → see `core/bus.py:EventType`.

---

## Mode State Machine

```
                 ┌──────────────────┐
         ┌──────▶│  companion_day   │◀──────┐
         │       └──────┬───────────┘       │
         │              │                   │
   (morning)     (user asks)          (user asks)
         │              │                   │
         │       ┌──────▼───────────┐  ┌────┴──────────────┐
         │       │ focus_assistant  │  │  idle_observer    │
         │       └──────────────────┘  └───────────────────┘
         │
   (schedule / manual)
         │       ┌──────────────────┐
         └───────│   night_watch    │
                 └──────┬───────────┘
                        │
                 (fault / overheat)
                        │
                 ┌──────▼───────────┐
                 │  safe_shutdown   │  (terminal — restart required)
                 └──────────────────┘
```

Any mode → `safe_shutdown` on:  safety_fault, overtemp, repeated actuator errors.

---

## Memory Schema (SQLite)

### episodic
| column      | type    | notes                       |
|-------------|---------|-----------------------------|
| id          | INTEGER | PK                          |
| ts          | REAL    | unix timestamp              |
| who         | TEXT    | person_id or "unknown"      |
| what        | TEXT    | brief description           |
| action      | TEXT    | robot action taken          |
| outcome     | TEXT    | success / fail / ignored    |
| confidence  | REAL    | 0..1                        |
| mode        | TEXT    | active mode at the time     |

### semantic_facts
| column      | type    | notes                       |
|-------------|---------|-----------------------------|
| id          | INTEGER | PK                          |
| key         | TEXT    | unique fact key             |
| value       | TEXT    | fact value                  |
| confidence  | REAL    | 0..1                        |
| count       | INTEGER | reinforcement count         |
| created_at  | REAL    |                             |
| last_seen   | REAL    |                             |

### social_profiles
| column              | type    |
|---------------------|---------|
| person_id           | TEXT PK |
| display_name        | TEXT    |
| familiarity         | REAL    | 0..1 |
| greeting_style      | TEXT    | "formal" / "casual" / "playful" |
| interruption_tolerance | REAL |
| usual_hours_start   | INTEGER | hour 0-23 |
| usual_hours_end     | INTEGER |
| interaction_count   | INTEGER |
| last_seen           | REAL    |
| notes               | TEXT    |

### procedural
| column      | type    | notes                       |
|-------------|---------|-----------------------------|
| id          | INTEGER | PK                          |
| skill       | TEXT    | skill name                  |
| param_key   | TEXT    |                             |
| param_value | TEXT    | JSON-encoded                |
| confidence  | REAL    |                             |
| updated_at  | REAL    |                             |

---

## Resource Budget (RPi 5 / 8 GB)

| Component           | RAM target | Notes                              |
|---------------------|------------|------------------------------------|
| OS + runtime        | ~700 MB    |                                    |
| Vision (OpenCV)     | ~150 MB    | no model loaded                    |
| Face embeddings     | ~200 MB    | lightweight model                  |
| STT (Vosk small-it) | ~80 MB     |                                    |
| LLM (llama3.2:1b)  | ~900 MB    | loaded on demand via ollama        |
| Vision LLM (moondream) | ~1.7 GB | kept_alive=0, unloaded after use   |
| **Total ceiling**   | **~3.8 GB** | leaves ~4.2 GB headroom           |

**Rules:**
- Never load two LLMs simultaneously → `_OLLAMA_GLOBAL_LOCK`
- Vision model always uses `keep_alive=0`
- Text model uses `keep_alive=120`s during conversations
- Skip inference if RAM free < 800 MB

---

## Night Watch Event Fusion

Events are never acted on in isolation. The fusion engine requires:

| Trigger Level | Required Combination                              | Response       |
|---------------|---------------------------------------------------|----------------|
| L0            | single motion ping                                | log only       |
| L1            | motion + camera confirms presence                 | orient, classify |
| L2            | L1 + unknown person OR repeated bursts            | quiet alert    |
| L3            | L2 + sustained presence > threshold OR sound      | external alert |

Anti-FP rules:
- 30-second cooldown between L1+ escalations
- Confidence threshold ≥ 0.65 for person classification
- 3-event minimum for "repeated burst"

---

## Phase Plan

| Phase | Contents                                         | Status   |
|-------|--------------------------------------------------|----------|
| 1     | Bus, modes, safety, motor, sensor, memory schema | **now**  |
| 2     | Vision (face detect/enroll), audio (wake/STT/TTS)| next     |
| 3     | Companion behavior, greeting, tracking, memory   | after    |
| 4     | Night watch, event fusion, alerts                | after    |
| 5     | Learning, micro-experiments, summarization       | later    |
| 6     | Testing, observability, dashboard v2             | last     |
