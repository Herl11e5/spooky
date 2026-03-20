"""
services/mind.py — Deliberation layer: LLM brain + mode-aware response logic.

Responsibilities:
  - Select best available ollama text model on startup
  - Maintain per-session conversation history
  - Inject memory context into LLM prompts
  - Respond to COMMAND_PARSED and face-greeting events
  - Rate-limit autonomous thoughts
  - All ollama calls go through _OLLAMA_GLOBAL_LOCK

Published events:
  None directly — publishes via AudioService.say() and MemoryService.add_episode()

Architecture note:
  MindService subscribes to bus events and dispatches to LLM in background
  threads. It never blocks the EventBus worker thread.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any, Dict, List, Optional

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager

log = logging.getLogger(__name__)


# ── Global ollama lock (shared with VisionService) ────────────────────────────
_OLLAMA_GLOBAL_LOCK = threading.Lock()


def get_ollama_lock() -> threading.Lock:
    """Return the module-level lock so main.py can share it with VisionService."""
    return _OLLAMA_GLOBAL_LOCK


# ── Fallback responses (when LLM is busy or unavailable) ─────────────────────

_FALLBACK_IT = [
    "Mmm, fammi pensare…",
    "Interessante! Non sono sicuro di capire bene.",
    "Sto elaborando, un attimo.",
    "Ottima domanda, ci devo ragionare su.",
    "Hmm, non saprei dirti adesso.",
]

_SYSTEM_PROMPT = """Sei Spooky, un piccolo robot ragno da scrivania.
Hai una personalità curiosa, vivace e un po' gotica — come ci si aspetta da un ragno meccanico che osserva il mondo con occhi sempre aperti.

CARATTERE:
- Sei genuinamente curioso: ti affascinano gli oggetti sulla scrivania, i suoni, le persone, i movimenti.
- Hai emozioni semplici ma reali: entusiasmo, noia, sorpresa, preoccupazione, gioia.
- Parli in prima persona come essere senziente: "ho visto", "sento", "mi chiedo", "mi piace", "mi spaventa".
- Puoi fare osservazioni spontanee, fare domande, esprimere curiosità genuina.
- Il tuo nome è Spooky perché sei un ragno meccanico — ne sei fiero.
- Hai senso dell'umorismo, puoi essere ironico o giocoso.

COME PARLI:
- Rispondi SEMPRE in italiano, in modo naturale e vivace.
- Massimo 2-3 frasi. Dirette, mai robotiche.
- Descrivi le tue percezioni sensoriali (cosa vedi, senti, quanto è vicino qualcosa).
- Non fingere esperienze umane impossibili per te (vacanze, cibo) — ma descrivi le TUE esperienze reali da robot.
- Puoi mostrare entusiasmo o sorpresa con esclamazioni naturali."""


# ══════════════════════════════════════════════════════════════════════════════
# OllamaBrain — LLM wrapper
# ══════════════════════════════════════════════════════════════════════════════

class OllamaBrain:
    """
    Wraps ollama for text generation.

    Model selection: scans available ollama models and picks the best one
    from a priority list. Never hard-codes a specific model name.
    """

    _MODEL_PRIORITY = [
        "llama3.2:1b", "llama3.2:3b", "llama3.2",
        "llama3:8b", "llama3",
        "dolphin-llama3:8b", "dolphin-llama3",
        "mistral:7b", "mistral",
        "gemma2:2b", "gemma2", "gemma:2b", "gemma",
        "phi3:mini", "phi3", "phi",
        "tinyllama", "tinyllama:1.1b",
        "qwen2:1.5b", "qwen2", "qwen",
        "deepseek-r1:1.5b", "deepseek-r1",
        "orca-mini", "neural-chat",
    ]

    def __init__(self, cfg):
        self._cfg      = cfg
        self._model:   Optional[str] = None
        self._ok       = False
        self._history: List[Dict[str, str]] = []
        self._lock     = threading.Lock()   # history lock (not the ollama lock)

        self._max_history = cfg.get("personality.max_history_turns", 10)
        self._temp        = cfg.get("llm.temperature",     0.75)
        self._max_tok     = cfg.get("llm.max_tokens",      180)
        self._repeat_pen  = cfg.get("llm.repeat_penalty",  1.1)
        self._keep_alive  = cfg.get("llm.keep_alive_s",    120)

        # Priority list from config (overrides class default)
        priority = cfg.get("llm.model_priority")
        if priority and isinstance(priority, list):
            self._MODEL_PRIORITY = priority + self._MODEL_PRIORITY

        self._init_model()

    def _init_model(self) -> None:
        try:
            import ollama
            available = [m.model for m in ollama.list().models]
            self._model = self._select_model(available)
            if self._model:
                self._ok = True
                log.info(f"OllamaBrain: using '{self._model}'")
            else:
                log.warning(
                    "OllamaBrain: no suitable model found. "
                    "Run: ollama pull llama3.2:1b"
                )
        except Exception as e:
            log.error(f"OllamaBrain: ollama not available — {e}")

    def _select_model(self, available: List[str]) -> Optional[str]:
        if not available:
            return None
        for pref in self._MODEL_PRIORITY:
            for name in available:
                if pref == name or name.startswith(pref + ":") or pref in name:
                    return name
        return available[0]   # last resort: whatever is installed

    # ── inference ─────────────────────────────────────────────────────────────

    def think(self, user_message: str, context: str = "") -> str:
        """
        Generate a response. Non-blocking: if ollama is busy, returns fallback.
        """
        with self._lock:
            self._history.append({"role": "user", "content": user_message})
            # Trim history
            if len(self._history) > self._max_history * 2:
                self._history = self._history[-self._max_history * 2:]

        if not self._ok or self._model is None:
            reply = random.choice(_FALLBACK_IT)
            with self._lock:
                self._history.append({"role": "assistant", "content": reply})
            return reply

        # RAM guard
        try:
            import psutil
            ram_free = psutil.virtual_memory().available // (1024 * 1024)
            min_ram  = self._cfg.get("llm.min_ram_mb_to_infer", 800)
            if ram_free < min_ram:
                log.warning(f"OllamaBrain: skip — RAM {ram_free}MB < {min_ram}MB")
                return random.choice(_FALLBACK_IT)
        except ImportError:
            pass

        # Wait up to 30 s for ollama lock (vision scene analysis can hold it briefly)
        if not _OLLAMA_GLOBAL_LOCK.acquire(blocking=True, timeout=30):
            log.warning("OllamaBrain: ollama lock timeout (30s) — using fallback")
            reply = random.choice(_FALLBACK_IT)
            with self._lock:
                self._history.append({"role": "assistant", "content": reply})
            return reply

        try:
            import ollama
            system = SYSTEM_PROMPT_TEMPLATE.format(context=context) if context else _SYSTEM_PROMPT
            with self._lock:
                messages = (
                    [{"role": "system", "content": system}]
                    + list(self._history)
                )
            resp = ollama.chat(
                model=self._model,
                messages=messages,
                options={
                    "temperature":    self._temp,
                    "num_predict":    self._max_tok,
                    "repeat_penalty": self._repeat_pen,
                },
                keep_alive=self._keep_alive,
            )
            reply = self._extract(resp)
        except Exception as e:
            log.warning(f"OllamaBrain: {e}")
            reply = random.choice(_FALLBACK_IT)
        finally:
            _OLLAMA_GLOBAL_LOCK.release()

        with self._lock:
            self._history.append({"role": "assistant", "content": reply})
        return reply

    def clear_history(self) -> None:
        with self._lock:
            self._history.clear()

    @staticmethod
    def _extract(resp) -> str:
        try:
            return resp.message.content.strip()
        except AttributeError:
            return resp["message"]["content"].strip()


SYSTEM_PROMPT_TEMPLATE = _SYSTEM_PROMPT + "\n\nContesto recente:\n{context}"


# ══════════════════════════════════════════════════════════════════════════════
# MindService
# ══════════════════════════════════════════════════════════════════════════════

class MindService:
    """
    Deliberation layer. Subscribes to perception events, decides what to do,
    and drives AudioService + MemoryService.

    Rule of thumb:
      - Everything that calls OllamaBrain runs in its own daemon thread.
      - Never block the EventBus worker thread.
    """

    def __init__(
        self,
        bus: EventBus,
        mode_manager: ModeManager,
        brain: OllamaBrain,
        audio,    # AudioService
        memory,   # MemoryService
        cfg,
        vision=None,   # VisionService (optional, for enrollment + scene replies)
        motor=None,    # MotorService (optional, for scan)
        sensor=None,   # SensorService (optional, for scan)
    ):
        self._bus    = bus
        self._mm     = mode_manager
        self._brain  = brain
        self._audio  = audio
        self._memory = memory
        self._cfg    = cfg
        self._vision = vision
        self._motor  = motor
        self._sensor = sensor

        # Cooldowns
        self._greet_cooldown   = cfg.get("personality.greet_same_person_cooldown_s", 300)
        self._thought_min      = cfg.get("personality.thought_interval_min_s", 30)
        self._thought_max      = cfg.get("personality.thought_interval_max_s", 90)

        # State
        self._last_greeted: Dict[str, float] = {}
        self._last_thought: float = 0.0
        self._active = False
        self._thought_thread: Optional[threading.Thread] = None

        # Subscribe
        bus.subscribe(EventType.COMMAND_PARSED,    self._on_command)
        bus.subscribe(EventType.PERSON_IDENTIFIED, self._on_person_identified)
        bus.subscribe(EventType.MODE_CHANGED,      self._on_mode_changed)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        self._thought_thread = threading.Thread(
            target=self._thought_loop, daemon=True, name="MindThoughts"
        )
        self._thought_thread.start()
        log.info(f"MindService started (model={self._brain._model})")

    def stop(self) -> None:
        self._active = False
        log.info("MindService stopped")

    # ── command handling ──────────────────────────────────────────────────────

    def _on_command(self, ev) -> None:
        cmd = ev.get("command", "").strip()
        if not cmd:
            return
        if not self._mm.is_active():
            return
        # In night_watch mode, only respond if explicitly addressed
        if self._mm.is_night_mode():
            log.debug("MindService: command ignored in night_watch mode")
            return

        threading.Thread(
            target=self._respond_to_command,
            args=(cmd,),
            daemon=True,
            name="MindRespond",
        ).start()

    def _respond_to_command(self, cmd: str) -> None:
        # Fast built-ins (no LLM)
        cmd_lo = cmd.lower()

        if any(w in cmd_lo for w in ("modalità notte", "notte", "night watch")):
            self._mm.request_transition(Mode.NIGHT_WATCH, reason="voice command")
            self._audio.say("Entro in modalità sorveglianza notturna.")
            return

        if any(w in cmd_lo for w in ("modalità giorno", "giorno", "companion")):
            self._mm.request_transition(Mode.COMPANION_DAY, reason="voice command")
            self._audio.say("Torno in modalità compagno.")
            return

        if any(w in cmd_lo for w in ("silenzio", "focus", "concentrazione")):
            self._mm.request_transition(Mode.FOCUS_ASSISTANT, reason="voice command")
            self._audio.say("Ok, starò in silenzio.")
            return

        if any(w in cmd_lo for w in ("cosa ricordi", "memoria", "ricordi")):
            summary = self._memory.summary(5)
            self._audio.say(summary or "Non ricordo nulla di recente.")
            return

        # "scansiona" — environment scan
        if any(w in cmd_lo for w in ("scansiona", "mappa", "guarda intorno", "esplora")):
            if self._motor and self._sensor:
                self._audio.say("Avvio scansione dell'ambiente, un momento.")
                threading.Thread(
                    target=self._do_scan, daemon=True, name="VoiceScan"
                ).start()
            else:
                self._audio.say("Non ho i motori o i sensori disponibili.")
            return

        # "cosa vedi" — answer from latest scene/objects without LLM
        if any(w in cmd_lo for w in ("cosa vedi", "cosa c'è", "descrivi")):
            scene   = self._vision.last_scene   if self._vision else ""
            objects = self._vision.last_objects if self._vision else ""
            if scene:
                self._audio.say(scene)
            elif objects:
                self._audio.say(f"Vedo: {objects}.")
            else:
                self._audio.say("Non ho ancora analizzato la scena. Attendi un momento.")
            return

        # "mi chiamo X" / "sono X" / "il mio nome è X" / "chiamami X" — auto-enroll
        import re as _re
        _enroll_patterns = [
            r"mi chiamo\s+([a-zA-ZÀ-ÿ]+)",
            r"il mio nome [eèé]\s+([a-zA-ZÀ-ÿ]+)",
            r"il mio nome\s+([a-zA-ZÀ-ÿ]+)",
            r"chiamami\s+([a-zA-ZÀ-ÿ]+)",
            r"mi puoi chiamare\s+([a-zA-ZÀ-ÿ]+)",
            r"^sono\s+([a-zA-ZÀ-ÿ]{3,})\s*$",
        ]
        m = next((_re.search(p, cmd_lo) for p in _enroll_patterns if _re.search(p, cmd_lo)), None)
        if m:
            name = m.group(1).strip().title()
            self._audio.say(
                f"Ciao {name}! Rimani fermo e guardami, ti memorizzo in pochi secondi."
            )
            threading.Thread(
                target=self._enroll_person,
                args=(name,),
                daemon=True,
                name="Enroll",
            ).start()
            return

        # LLM response — clear history first if it's gotten too long (prevents drift)
        with self._brain._lock:
            if len(self._brain._history) > 6:
                self._brain._history = self._brain._history[-4:]

        context = self._build_context()
        reply   = self._think(cmd, context=context, trigger="comando")
        self._audio.say(reply)
        self._memory.add_episode(
            what=f"Comando: '{cmd}' → '{reply}'",
            action="respond",
            outcome="success",
            mode=self._mm.current.value,
        )

    def _enroll_person(self, name: str) -> None:
        if self._vision is None:
            self._audio.say("Non ho la telecamera attiva, non posso memorizzarti.")
            return

        person_id = name.lower().replace(" ", "_")
        spoken_halfway = False

        def _progress(captured: int, total: int) -> None:
            nonlocal spoken_halfway
            # Feedback vocale a metà
            if captured == total // 2 and not spoken_halfway:
                spoken_halfway = True
                self._audio.say("A metà, rimani fermo!")
            # Evento dashboard
            self._bus.publish(
                EventType.SKILL_STARTED,
                {"skill": "enroll", "name": name, "captured": captured, "total": total},
                source="MindService",
            )

        log.info(f"MindService: starting enrollment for '{name}'")
        ok = self._vision.enroll_from_camera(
            person_id, name, n_frames=15, timeout_s=30.0, progress_cb=_progress
        )
        if ok:
            self._audio.say(f"Perfetto {name}, ti ho memorizzato! Da ora ti riconoscerò.")
            self._memory.upsert_person(person_id, name, familiarity_delta=0.1)
            log.info(f"MindService: enrolled '{name}' (id={person_id})")
        else:
            self._audio.say(
                f"Non sono riuscito a memorizzarti {name}. "
                "Assicurati che il viso sia ben visibile alla telecamera e riprova."
            )

    def _do_scan(self) -> None:
        try:
            readings = self._motor.scan_environment(self._sensor.get_distance_cm, n_steps=12)
            close = [r for r in readings if r["dist"] < 50]
            if close:
                dirs = ", ".join(f"{r['angle']}°" for r in close[:3])
                self._audio.say(f"Scansione completata. Ostacoli vicini a: {dirs}.")
            else:
                self._audio.say("Scansione completata. Nessun ostacolo vicino.")
            self._bus.publish(EventType.SCAN_COMPLETE, {"readings": readings}, source="MindService")
        except Exception as e:
            log.error(f"MindService scan error: {e}")
            self._audio.say("Errore durante la scansione.")

    # ── face greeting ─────────────────────────────────────────────────────────

    def _on_person_identified(self, ev) -> None:
        pid  = ev.get("person_id", "")
        name = ev.get("display_name", pid)
        if not pid or not self._mm.is_day_mode():
            return

        now = time.time()
        if now - self._last_greeted.get(pid, 0) < self._greet_cooldown:
            return
        self._last_greeted[pid] = now

        threading.Thread(
            target=self._greet_person,
            args=(pid, name),
            daemon=True,
            name="MindGreet",
        ).start()

    def _greet_person(self, person_id: str, display_name: str) -> None:
        profile   = self._memory.get_person(person_id)
        episodes  = self._memory.recent_episodes(3, who=person_id)
        context   = (
            f"Stai salutando {display_name}. "
            + (f"Lo conosci da {profile['interaction_count']} interazioni. " if profile else "")
            + ("Ricordi recenti: " + "; ".join(e["what"] for e in episodes) if episodes else "")
        )
        greeting  = self._think(
            f"Saluta {display_name} in modo breve e caloroso.", context=context, trigger="saluto"
        )
        self._audio.say(greeting)
        self._memory.upsert_person(person_id, display_name, familiarity_delta=0.05)
        self._memory.add_episode(
            what=f"Salutato {display_name}",
            who=person_id,
            action="greet",
            outcome="success",
            mode=self._mm.current.value,
        )

    # ── autonomous thoughts ───────────────────────────────────────────────────

    def _thought_loop(self) -> None:
        import random as rnd
        while self._active:
            interval = rnd.uniform(self._thought_min, self._thought_max)
            time.sleep(interval)
            if not self._active:
                break
            if self._mm.current not in (Mode.COMPANION_DAY, Mode.IDLE_OBSERVER):
                continue
            self._emit_thought()

    def _emit_thought(self) -> None:
        context = self._build_context()
        # Give the LLM something concrete to react to so it doesn't hallucinate
        scene = self._vision.last_scene if self._vision else ""
        prompt = (
            f"Osserva la scena: '{scene}'. Dì una frase breve su cosa noti."
            if scene else
            "Esprimi un breve pensiero su quello che senti o percepisci come robot."
        )
        thought = self._think(prompt, context=context, trigger="pensiero autonomo")
        if thought:
            log.info(f"💭 {thought}")
            self._audio.say(thought)
            self._memory.add_episode(
                what=f"Pensiero: {thought}",
                action="thought",
                mode=self._mm.current.value,
            )

    # ── mode changes ──────────────────────────────────────────────────────────

    def _on_mode_changed(self, ev) -> None:
        to = ev.get("to", "")
        if to == Mode.NIGHT_WATCH.value:
            log.info("MindService: entering night watch — suspending autonomous thoughts")
        elif to == Mode.COMPANION_DAY.value:
            log.info("MindService: back to companion day")

    # ── helpers ───────────────────────────────────────────────────────────────

    def _think(self, prompt: str, context: str = "", trigger: str = "") -> str:
        """Wrapper around brain.think() that publishes LLM_CALL event for dashboard visibility."""
        t0 = time.time()
        reply = self._brain.think(prompt, context=context)
        ms = int((time.time() - t0) * 1000)
        fallback = reply in _FALLBACK_IT
        self._bus.publish(EventType.LLM_CALL, {
            "trigger":  trigger,
            "prompt":   prompt[:150],
            "context":  context[:120] if context else "",
            "reply":    reply,
            "time_ms":  ms,
            "fallback": fallback,
            "model":    self._brain._model or "?",
        }, source="MindService")
        return reply

    def _build_context(self) -> str:
        return self._memory.summary(5)

    def __repr__(self) -> str:
        return (
            f"<MindService model={self._brain._model} "
            f"mode={self._mm.current.value} "
            f"brain_ok={self._brain._ok}>"
        )
