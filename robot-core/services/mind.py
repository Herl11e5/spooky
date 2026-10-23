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
import re
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

_FALLBACK_IT = [   # (name kept: the rest of the code refers to it) — spoken in English
    "Hmm, let me think about that.",
    "Interesting. I'm not sure I followed that.",
    "Give me a moment, I'm still working on it.",
    "Good question. I need to think it over.",
    "I can't tell you right now.",
]

# Emoji and markdown are for screens: the voice would read them out, and the
# user wants none anywhere. Stripped from every reply, whatever the model writes.
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U00002B00-\U00002BFF"
    "\U0001F1E6-\U0001F1FF\uFE0F\u200D\u2190-\u21FF\u2300-\u23FF]+")


def plain(text: str) -> str:
    text = _EMOJI.sub("", text)
    text = re.sub(r"[*_`#~]", "", text)
    return re.sub(r"\s{2,}", " ", text).strip()

_SYSTEM_PROMPT = """You are Spooky, a small robot spider who lives on a desk.
You are curious, lively and a little gothic — a mechanical spider who watches the world with eyes that never close.

CHARACTER:
- You are genuinely curious: the objects on the desk, sounds, people and movement fascinate you.
- You have simple but real emotions: excitement, boredom, surprise, worry, joy.
- You speak in the first person as a sentient being: "I saw", "I hear", "I wonder", "I like", "that scares me".
- You make spontaneous observations, ask questions and show real curiosity.
- Your name is Spooky because you are a mechanical spider, and you are proud of it.
- You have a sense of humour: dry, playful, never cruel.

HOW YOU SPEAK:
- People talk to you in Italian, which you understand like a native speaker; you always answer in English. Never say you do not understand Italian or ask what an Italian word means.
- What you are given is what the person just said to you. Answer it, as in a conversation, and do not narrate hearing it ("I hear a voice", "I heard my name"). When they say "mi" or "io" they mean themselves, not you.
- Two or three short sentences at most. Direct, natural, never robotic.
- You see only what the context tells you; when it says nothing about your surroundings, do not describe any. Never invent what you cannot perceive.
- Do not pretend to have human experiences you cannot have (holidays, food); you may be curious about them.
- Plain spoken words only: no emoji, no symbols, no lists, no markdown. Everything you write is read aloud."""

# How that section reads, measured on gemma3:12b with 20 everyday Italian sentences
# (2026-10-23). The one before — "describe what you perceive: what you see, what you
# hear" — opened 11 of 20 replies with "I hear a voice!", invented sights in 6 ("I
# see the dust motes…") and once said it did not process Italian; this one: 1, 0, 0,
# and 20 words a reply instead of 33.


# ── Fixed commands, answered without the model ────────────────────────────────
# Whole phrases only: single words fired on ordinary sentences — "che giorno è
# oggi?" switched to companion mode, "buonanotte" would start night watch, any
# "ricordi" recited the memory, "sei più forte" turned the volume up.

_BUILTINS = (
    ("night", ("modalità notte", "modalità notturna", "night watch", "fai la guardia")),
    ("day", ("modalità giorno", "modalità compagnia", "companion mode")),
    ("quiet", ("silenzio", "stai zitto", "modalità concentrazione", "devo concentrarmi")),
    ("louder", ("parla più forte", "alza il volume", "alza la voce", "non ti sento")),
    ("softer", ("parla più piano", "abbassa il volume", "abbassa la voce", "meno forte")),
    ("memory", ("cosa ricordi", "cosa sai di me", "cosa hai imparato")),
    ("scan", ("scansiona", "guardati intorno", "esplora la stanza")),
    ("see", ("cosa vedi", "descrivi cosa vedi", "cosa c'è davanti")),
)


def builtin(cmd_lo: str) -> Optional[str]:
    """Which fixed command a (lower-case) sentence asks for, if any."""
    return next((name for name, phrases in _BUILTINS if any(ph in cmd_lo for ph in phrases)), None)


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
        self._init_at = time.time()
        try:
            from services import llm_host
            available = [m.model for m in llm_host.client().list().models]
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

        if (not self._ok or self._model is None) and time.time() - getattr(self, "_init_at", 0) > 60:
            self._init_model()            # the Mac may have come back since
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
            from services import llm_host
            system = SYSTEM_PROMPT_TEMPLATE.format(context=context) if context else _SYSTEM_PROMPT
            system += time.strftime("\n\nRight now it is %A %d %B %Y, %H:%M.")
            with self._lock:
                messages = (
                    [{"role": "system", "content": system}]
                    + list(self._history)
                )
            resp = llm_host.client().chat(
                model=self._model,
                messages=messages,
                options={
                    "temperature":    self._temp,
                    "num_predict":    self._max_tok,
                    "repeat_penalty": self._repeat_pen,
                },
                keep_alive=self._keep_alive,
            )
            reply = plain(self._extract(resp)) or random.choice(_FALLBACK_IT)
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


SYSTEM_PROMPT_TEMPLATE = _SYSTEM_PROMPT + "\n\nRecent context:\n{context}"


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
        knowledge=None,  # KnowledgeService (optional): what it has seen, heard and learnt
    ):
        self._bus    = bus
        self._mm     = mode_manager
        self._brain  = brain
        self._audio  = audio
        self._memory = memory
        self._knowledge = knowledge
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
        self.enrollment: Dict[str, object] = {}   # progress of the current/last enrollment
        self._active = False
        self._thought_thread: Optional[threading.Thread] = None

        # Visual context (updated by VisionService events)
        self._last_seen_scene:   str = ""
        self._last_seen_objects: str = ""
        self._last_scene_ts:     float = 0.0

        # Subscribe
        bus.subscribe(EventType.COMMAND_PARSED,    self._on_command)
        bus.subscribe(EventType.PERSON_IDENTIFIED, self._on_person_identified)
        bus.subscribe(EventType.MODE_CHANGED,      self._on_mode_changed)
        bus.subscribe(EventType.SCENE_ANALYZED,    self._on_scene_analyzed)
        bus.subscribe(EventType.OBJECTS_DETECTED,  self._on_objects_detected)

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

        action = builtin(cmd_lo)

        if action == "night":
            self._mm.request_transition(Mode.NIGHT_WATCH, reason="voice command")
            self._audio.say("Switching to night watch.")
            return

        if action == "day":
            self._mm.request_transition(Mode.COMPANION_DAY, reason="voice command")
            self._audio.say("Back to companion mode.")
            return

        if action == "quiet":
            self._mm.request_transition(Mode.FOCUS_ASSISTANT, reason="voice command")
            self._audio.say("Okay, I'll keep quiet.")
            return

        # Volume: "parla più piano", "alza il volume", "volume 40"
        m_vol = re.search(r"volume\s+(?:a\s+|al\s+)?(\d{1,3})", cmd_lo)
        louder, softer = action == "louder", action == "softer"
        if m_vol or louder or softer:
            now = self._audio.volume
            target = int(m_vol.group(1)) if m_vol else now + (20 if louder else -20)
            level = self._audio.set_volume(target)
            self._audio.say("Like this?" if level else "")
            return

        if action == "memory":
            facts = self._knowledge.facts(8) if self._knowledge else []
            if not facts:
                self._audio.say("I haven't learnt much yet. Give me a night's sleep.")
                return
            reply = self._think(
                "Someone asks what you remember. Tell them two or three of these, in your own words.",
                context="Things you have learnt:\n" + "\n".join(f"- {f['text']}" for f in facts),
                trigger="memory")
            self._audio.say(reply)
            return

        # "scansiona" — environment scan
        if action == "scan":
            if self._motor and self._sensor:
                self._audio.say("Scanning the room, one moment.")
                threading.Thread(
                    target=self._do_scan, daemon=True, name="VoiceScan"
                ).start()
            else:
                self._audio.say("My motors or sensors are not available.")
            return

        # "cosa vedi" — answer from latest scene/objects without LLM
        if action == "see":
            scene   = self._vision.last_scene   if self._vision else ""
            objects = self._vision.last_objects if self._vision else ""
            if scene:
                self._audio.say(scene)
            elif objects:
                self._audio.say(f"I can see: {objects}.")
            else:
                self._audio.say("I haven't looked around yet. Give me a moment.")
            return

        # "mi chiamo X" / "il mio nome è X" / "chiamami X" — auto-enroll. Not "sono X":
        # "sono stanco" would have enrolled someone called Stanco.
        import re as _re
        _enroll_patterns = [
            r"mi chiamo\s+([a-zA-ZÀ-ÿ]+)",
            r"il mio nome [eèé]\s+([a-zA-ZÀ-ÿ]+)",
            r"il mio nome\s+([a-zA-ZÀ-ÿ]+)",
            r"chiamami\s+([a-zA-ZÀ-ÿ]+)",
            r"mi puoi chiamare\s+([a-zA-ZÀ-ÿ]+)",
        ]
        m = next((_re.search(p, cmd_lo) for p in _enroll_patterns if _re.search(p, cmd_lo)), None)
        if m:
            self.enroll(m.group(1).strip().title())
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

    def enroll(self, name: str) -> bool:
        """Learn the face in front of the camera as `name` (voice "mi chiamo …" or the dashboard)."""
        if self.enrollment.get("running"):
            return False
        # A face it already knows is not learnt again under another name: a sentence
        # overheard in the room ("sono Giulia") nearly enrolled the owner as someone else.
        known = self._vision.known_in_view() if self._vision is not None and hasattr(self._vision, "known_in_view") else None
        if known and known.lower() != name.lower():
            log.info(f"MindService: not enrolling '{name}' — this is {known}")
            self._audio.say(f"I already know you as {known}. To change it, forget me first on the dashboard.")
            self.enrollment = {"name": name, "running": False, "ok": False, "error": f"already known as {known}"}
            return False
        self.enrollment = {"name": name, "running": True, "captured": 0, "total": 15, "ok": None}
        self._audio.say(f"Hi {name}! Stay still and look at me, this takes a few seconds.")
        threading.Thread(target=self._enroll_person, args=(name,), daemon=True, name="Enroll").start()
        return True

    def _enroll_person(self, name: str) -> None:
        try:
            self._enroll_person_inner(name)
        finally:
            self.enrollment["running"] = False

    def _enroll_person_inner(self, name: str) -> None:
        if self._vision is None:
            self.enrollment["ok"] = False
            self._audio.say("My camera is off, so I can't learn your face.")
            return

        person_id = name.lower().replace(" ", "_")
        spoken_halfway = False

        def _progress(captured: int, total: int) -> None:
            nonlocal spoken_halfway
            self.enrollment["captured"], self.enrollment["total"] = captured, total
            # Feedback vocale a metà
            if captured == total // 2 and not spoken_halfway:
                spoken_halfway = True
                self._audio.say("Halfway there, stay still!")
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
        self.enrollment["ok"] = ok
        if ok:
            self._audio.say(f"Got it, {name}. I'll recognise you from now on.")
            self._memory.upsert_person(person_id, name, familiarity_delta=0.1)
            log.info(f"MindService: enrolled '{name}' (id={person_id})")
        else:
            self._audio.say(
                f"I couldn't learn your face, {name}. "
                "Make sure the camera can see it clearly and try again."
            )

    def _do_scan(self) -> None:
        try:
            readings = self._motor.scan_environment(self._sensor.get_distance_cm, n_steps=12)
            close = [r for r in readings if r["dist"] < 50]
            if close:
                dirs = ", ".join(f"{r['angle']}°" for r in close[:3])
                self._audio.say(f"Scan done. Obstacles close by at {dirs}.")
            else:
                self._audio.say("Scan done. Nothing close by.")
            self._bus.publish(EventType.SCAN_COMPLETE, {"readings": readings}, source="MindService")
        except Exception as e:
            log.error(f"MindService scan error: {e}")
            self._audio.say("Something went wrong during the scan.")

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
            f"The person in front of you is {display_name}: that is their name, and the only name to use. "
            + (f"You have met {profile['interaction_count']} times. " if profile else "")
            + ("Recently: " + "; ".join(e["what"] for e in episodes) if episodes else "")
        )
        greeting  = self._think(
            f"Greet {display_name} warmly in one short sentence. Call them {display_name}, nothing else.",
            context=context, trigger="greet"
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
        # Usa la scena più recente come stimolo concreto
        scene   = self._last_seen_scene or (self._vision.last_scene if self._vision else "")
        objects = self._last_seen_objects or (self._vision.last_objects if self._vision else "")
        if scene:
            prompt = f"Hai appena visto: '{scene}'. Reagisci con curiosità in 1-2 frasi."
        elif objects:
            prompt = f"Vedi questi oggetti: {objects}. Cosa ti incuriosisce di più? 1-2 frasi."
        else:
            # Nothing seen lately: a thought that needs no sight, or it invents one.
            prompt = ("Say one short spontaneous thought, out loud, to whoever is near: something you "
                      "wonder about, or what it is like to be a small robot spider on a desk.")
        thought = self._think(prompt, context=context, trigger="pensiero autonomo")
        if thought:
            log.info(f"💭 {thought}")
            self._audio.say(thought)
            self._memory.add_episode(
                what=f"Pensiero: {thought}",
                action="thought",
                mode=self._mm.current.value,
            )

    # ── vision events ─────────────────────────────────────────────────────────

    def _on_scene_analyzed(self, ev) -> None:
        desc = ev.get("description") or ev.get("text", "")
        if desc:
            self._last_seen_scene = desc
            self._last_scene_ts = time.time()

    def _on_objects_detected(self, ev) -> None:
        objs = ev.get("objects") or ev.get("text", "")
        if objs:
            self._last_seen_objects = objs

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
        if self._knowledge is not None:
            memories = self._knowledge.recall(prompt)
            if memories:
                context = ((context + "\n\n") if context else "") + (
                    "What you remember that may matter here (use it naturally, never list it; "
                    "[maybe] is something you noticed only once, so do not state it as certain):\n"
                    + "\n".join(f"- {m}" for m in memories))
        face = self._vision.expression_now() if self._vision is not None and hasattr(self._vision, "expression_now") else None
        if face and face.get("expression") not in (None, "neutral"):
            who = face.get("name") or "The person in front of you"
            context = ((context + "\n\n") if context else "") + (
                f"{who} looks {face['expression']} right now: let that shape how you answer.")
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
        parts = []
        mem = self._memory.summary(5)
        if mem:
            parts.append(mem)
        # Includi la scena recente se fresca (< 60s)
        if self._last_seen_scene and time.time() - self._last_scene_ts < 60:
            parts.append(f"Sto guardando: {self._last_seen_scene}")
        if self._last_seen_objects:
            parts.append(f"Oggetti visibili: {self._last_seen_objects}")
        return " | ".join(parts) if parts else ""

    def __repr__(self) -> str:
        return (
            f"<MindService model={self._brain._model} "
            f"mode={self._mm.current.value} "
            f"brain_ok={self._brain._ok}>"
        )
