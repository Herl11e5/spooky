"""
services/brain.py — Spooky's "life engine". Unified behavior loop.

Replaces PersonalityService + EmotionService.

Emotional state (Russell's circumplex):
  energy   (0-1):  arousal — 1=excited, 0=sleepy
  valence  (-1,1): affect  — +1=happy, -1=scared

Behavior queue priorities:
  10 = CRITICAL  (picked_up, danger)
   7 = REACTIVE  (startle, person appeared)
   5 = SOCIAL    (wake word, command received)
   3 = EXPRESSIVE (novelty, curiosity)
   1 = AMBIENT   (idle micro-movements — ALWAYS fires)

The ambient loop guarantees Spooky is NEVER completely still.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from typing import List, Optional, Tuple

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager

log = logging.getLogger(__name__)


class BrainService:
    """
    Central behavior director. The "life" of Spooky.
    Runs at 4 Hz. Picks and plays choreography sequences based on
    emotional state and incoming events.
    """

    _TICK_S = 0.25   # 4 Hz

    # Ambient behavior cadence
    _AMBIENT_MIN_S = 1.5
    _AMBIENT_MAX_S = 5.0

    # After this many seconds alone → boredom behaviors
    _BORED_AFTER_S = 180.0

    def __init__(
        self,
        bus: EventBus,
        mode_manager: ModeManager,
        motor,
        choreography,
        audio,
    ):
        self._bus    = bus
        self._mm     = mode_manager
        self._motor  = motor
        self._choreo = choreography
        self._audio  = audio

        self._lock   = threading.RLock()
        self._active = False
        self._thread: Optional[threading.Thread] = None

        # ── emotional state ───────────────────────────────────────────────────
        self._energy  = 0.65   # 0-1
        self._valence = 0.20   # -1 to +1

        # ── presence & timing ─────────────────────────────────────────────────
        self._person_present = False
        self._alone_since    = time.time()
        self._speaking       = False

        # ── ambient timing ────────────────────────────────────────────────────
        self._last_ambient  = 0.0
        self._next_interval = random.uniform(2.0, 4.0)

        # ── reaction queue: [(priority, sequence_name)] ───────────────────────
        self._reactions: List[Tuple[int, str]] = []

        # ── bus subscriptions ─────────────────────────────────────────────────
        bus.subscribe(EventType.PERSON_DETECTED,    self._on_person_detected)
        bus.subscribe(EventType.PERSON_LOST,        self._on_person_lost)
        bus.subscribe(EventType.UNUSUAL_SOUND,      self._on_startle)
        bus.subscribe(EventType.MOTION_DETECTED,    self._on_motion)
        bus.subscribe(EventType.PICKED_UP,          self._on_picked_up)
        bus.subscribe(EventType.PUT_DOWN,           self._on_put_down)
        bus.subscribe(EventType.WAKE_WORD_DETECTED, self._on_wake_word)
        bus.subscribe(EventType.COMMAND_PARSED,     self._on_command)
        bus.subscribe(EventType.SAFETY_FAULT,       self._on_danger)
        bus.subscribe(EventType.SCENE_ANALYZED,     self._on_novelty)
        bus.subscribe(EventType.TTS_STARTED,        self._on_tts_started)
        bus.subscribe(EventType.TTS_FINISHED,       self._on_tts_finished)

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="Brain"
        )
        self._thread.start()
        log.info("BrainService started")

    def stop(self) -> None:
        self._active = False
        if self._thread:
            self._thread.join(timeout=3.0)
        log.info("BrainService stopped")

    # ── public API ─────────────────────────────────────────────────────────────

    @property
    def mood(self) -> str:
        """Human-readable current mood based on energy + valence."""
        e, v = self._energy, self._valence
        if v < -0.4:              return "wary"
        if e < 0.3 and v < -0.1: return "bored"
        if e < 0.3:               return "content"
        if e > 0.65 and v > 0.4: return "playful"
        if e > 0.55 and v > 0.1: return "happy"
        if e > 0.55:              return "curious"
        return "content"

    def push_reaction(self, priority: int, sequence: str) -> None:
        """Queue a choreography reaction (thread-safe)."""
        with self._lock:
            self._reactions.append((priority, sequence))
            self._reactions.sort(key=lambda x: -x[0])

    # ── main loop ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._active:
            t0 = time.time()
            try:
                self._tick()
            except Exception as e:
                log.error(f"Brain tick error: {e}")
            elapsed = time.time() - t0
            time.sleep(max(0.0, self._TICK_S - elapsed))

    def _tick(self) -> None:
        # Slowly decay energy and valence toward calm baseline
        with self._lock:
            self._energy  += (0.55 - self._energy)  * 0.004
            self._valence += (0.10 - self._valence) * 0.003

        # 1. Execute highest-priority pending reaction
        with self._lock:
            reaction = self._reactions.pop(0) if self._reactions else None

        if reaction is not None:
            _, seq = reaction
            try:
                self._choreo.play(seq, wait=False)
            except Exception as e:
                log.debug(f"Brain reaction '{seq}': {e}")
            return

        # 2. Skip ambient while speaking or in wrong mode
        if self._speaking:
            return
        if self._mm.current not in (Mode.COMPANION_DAY, Mode.IDLE_OBSERVER):
            return

        # 3. Ambient behavior on schedule
        now = time.time()
        if now - self._last_ambient < self._next_interval:
            return

        seq = self._pick_ambient()
        try:
            self._choreo.play(seq, wait=False)
        except Exception as e:
            log.debug(f"Brain ambient '{seq}': {e}")

        self._last_ambient = now
        # High energy → shorter interval before next move
        energy_factor = 1.0 - self._energy * 0.45
        self._next_interval = (
            random.uniform(self._AMBIENT_MIN_S, self._AMBIENT_MAX_S) * energy_factor
        )

        # Publish current mood for dashboard
        self._bus.publish(
            EventType.PERSONALITY_MOOD_CHANGED,
            {"mood": self.mood, "intensity": round(self._energy, 2)},
            source="brain",
        )

    def _pick_ambient(self) -> str:
        """Select ambient behavior based on current state."""
        alone_for = time.time() - self._alone_since
        e = self._energy

        # Boredom tier — alone too long
        if alone_for > self._BORED_AFTER_S:
            return random.choice([
                "bored_sigh", "bored_sigh",
                "peek_left", "peek_right",
                "look_around", "double_take",
                "wake_up_stretch",
            ])

        # Person present — attentive micro-expressions
        if self._person_present:
            return random.choice([
                "head_tilt_left", "head_tilt_right",
                "curious", "nod", "fidget",
                "head_tilt_left", "head_tilt_right",  # weighted
            ])

        # High energy — exploring and investigating
        if e > 0.6:
            return random.choice([
                "investigate", "sniff",
                "head_tilt_left", "head_tilt_right",
                "peek_left", "peek_right",
                "look_around", "double_take",
                "fidget", "fidget",
            ])

        # Baseline — always alive, always moving
        return random.choice([
            "fidget", "fidget", "fidget",
            "head_tilt_left", "head_tilt_right",
            "curious", "thinking",
        ])

    # ── event handlers ─────────────────────────────────────────────────────────

    def _on_person_detected(self, ev) -> None:
        first_appearance = not self._person_present
        with self._lock:
            self._person_present = True
            self._valence = min(1.0, self._valence + 0.3)
            self._energy  = min(1.0, self._energy  + 0.2)
        if first_appearance:
            self.push_reaction(7, "greeting")

    def _on_person_lost(self, ev) -> None:
        with self._lock:
            self._person_present = False
            self._alone_since = time.time()

    def _on_startle(self, ev) -> None:
        self.push_reaction(7, "startle")
        with self._lock:
            self._energy = min(1.0, self._energy + 0.4)

    def _on_motion(self, ev) -> None:
        if not self._person_present:
            self.push_reaction(6, "alert")

    def _on_picked_up(self, ev) -> None:
        with self._lock:
            self._reactions.clear()
            self._energy = min(1.0, self._energy + 0.6)
        self.push_reaction(10, "picked_up_react")

    def _on_put_down(self, ev) -> None:
        self.push_reaction(8, "put_down_react")

    def _on_wake_word(self, ev) -> None:
        """Snap to attention when name is called."""
        self.push_reaction(7, "alert")
        with self._lock:
            self._energy = min(1.0, self._energy + 0.25)

    def _on_command(self, ev) -> None:
        """Thinking pose while processing command."""
        self.push_reaction(5, "thinking")

    def _on_danger(self, ev) -> None:
        with self._lock:
            self._reactions.clear()
            self._valence = max(-1.0, self._valence - 0.5)
        self.push_reaction(10, "startle")

    def _on_novelty(self, ev) -> None:
        self.push_reaction(3, "curious")
        with self._lock:
            self._energy = min(1.0, self._energy + 0.1)

    def _on_tts_started(self, ev) -> None:
        self._speaking = True

    def _on_tts_finished(self, ev) -> None:
        self._speaking = False
        # Small acknowledgment nod after speaking
        self.push_reaction(2, "nod")

    def __repr__(self) -> str:
        return (
            f"<BrainService mood={self.mood} "
            f"energy={self._energy:.2f} valence={self._valence:.2f} "
            f"person={'yes' if self._person_present else 'no'}>"
        )
