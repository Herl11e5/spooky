"""
services/conscience.py — Internal state / personality layer.

Tracks drives that influence the robot's behavioral choices:
  energy              0–1  decreases with activity, recovers over time
  social_drive        0–1  increases while alone, decreases after interaction
  curiosity           0–1  spikes on novel events, decays slowly
  attention           0–1  focus on current person or task
  interaction_fatigue 0–1  accumulates per conversation turn, decays between sessions

These values are NOT exposed directly to the LLM. Instead, MindService
reads them to gate decisions:
  - should_speak()   → avoid interrupting when fatigue is high
  - should_explore() → drive idle patrol / look-around when curious
  - is_social()      → greet enthusiastically vs quietly

Publishes no events itself — purely read by other services.

Design: all fields decay/grow on a 1-second tick thread.
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager

log = logging.getLogger(__name__)


@dataclass
class DriveState:
    energy:              float = 0.9
    social_drive:        float = 0.5
    curiosity:           float = 0.6
    attention:           float = 0.0
    interaction_fatigue: float = 0.0

    def __repr__(self) -> str:
        return (
            f"energy={self.energy:.2f} social={self.social_drive:.2f} "
            f"curious={self.curiosity:.2f} attn={self.attention:.2f} "
            f"fatigue={self.interaction_fatigue:.2f}"
        )


class Conscience:
    """
    Runs a 1-second background tick that naturally evolves drives.
    Call notify_*() methods on events to create meaningful drive changes.
    """

    # Decay/growth rates per second
    _ENERGY_RECOVERY         = 0.002   # slow recovery at rest
    _ENERGY_COST_MOTION      = 0.008   # per second of motion
    _ENERGY_COST_SPEECH      = 0.010   # per TTS utterance
    _SOCIAL_GROWTH_ALONE     = 0.003   # grow while no person present
    _SOCIAL_DECAY_INTERACT   = 0.020   # drop on interaction
    _CURIOSITY_DECAY         = 0.001   # slow decay
    _CURIOSITY_SPIKE         = 0.30    # on novel event
    _ATTENTION_DECAY         = 0.005   # attention fades
    _FATIGUE_PER_TURN        = 0.10    # per conversation turn
    _FATIGUE_RECOVERY        = 0.002   # slow recovery between sessions

    # Thresholds for gating decisions
    SPEAK_FATIGUE_MAX   = 0.70   # above this → prefer silence
    SPEAK_ENERGY_MIN    = 0.15   # below this → low-energy, minimal speech
    EXPLORE_CURIOSITY   = 0.65   # above this → may start patrol/look-around
    GREET_SOCIAL_MIN    = 0.35   # below this → skip spontaneous greeting

    def __init__(self, bus: EventBus, mode_manager: ModeManager):
        self._bus   = bus
        self._mm    = mode_manager
        self._state = DriveState()
        self._lock  = threading.RLock()
        self._active = False
        self._thread: Optional[threading.Thread] = None
        self._person_present = False

        bus.subscribe(EventType.PERSON_DETECTED,   self._on_person_detected)
        bus.subscribe(EventType.PERSON_LOST,        self._on_person_lost)
        bus.subscribe(EventType.TTS_STARTED,        self._on_tts)
        bus.subscribe(EventType.COMMAND_PARSED,     self._on_command)
        bus.subscribe(EventType.SCENE_ANALYZED,     self._on_novel_event)
        bus.subscribe(EventType.MOTION_DETECTED,    self._on_novel_event)
        bus.subscribe(EventType.UNUSUAL_SOUND,      self._on_novel_event)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        self._thread = threading.Thread(
            target=self._tick_loop, daemon=True, name="Conscience"
        )
        self._thread.start()
        log.info("Conscience started")

    def stop(self) -> None:
        self._active = False
        log.info("Conscience stopped")

    # ── public reads ──────────────────────────────────────────────────────────

    @property
    def state(self) -> DriveState:
        with self._lock:
            return DriveState(
                energy              = self._state.energy,
                social_drive        = self._state.social_drive,
                curiosity           = self._state.curiosity,
                attention           = self._state.attention,
                interaction_fatigue = self._state.interaction_fatigue,
            )

    def should_speak(self) -> bool:
        """True if conditions favour speaking (not fatigued, enough energy)."""
        s = self._state
        return s.interaction_fatigue < self.SPEAK_FATIGUE_MAX and s.energy > self.SPEAK_ENERGY_MIN

    def should_greet(self) -> bool:
        return self._state.social_drive >= self.GREET_SOCIAL_MIN and self.should_speak()

    def should_explore(self) -> bool:
        """True if curiosity is high enough to trigger patrol/look-around."""
        return (
            self._state.curiosity >= self.EXPLORE_CURIOSITY
            and not self._person_present
            and self._mm.current in (Mode.COMPANION_DAY, Mode.IDLE_OBSERVER)
        )

    def energy_label(self) -> str:
        e = self._state.energy
        if e > 0.70: return "high"
        if e > 0.35: return "medium"
        return "low"

    def mood_label(self) -> str:
        s = self._state
        if s.interaction_fatigue > 0.6:  return "tired"
        if s.curiosity > 0.7:            return "curious"
        if s.social_drive > 0.7:         return "lonely"
        if s.energy < 0.25:              return "drowsy"
        return "content"

    # ── external notifications ────────────────────────────────────────────────

    def notify_motion_used(self, seconds: float) -> None:
        with self._lock:
            self._state.energy = max(0.0,
                self._state.energy - self._ENERGY_COST_MOTION * seconds)

    def notify_person(self, present: bool) -> None:
        with self._lock:
            self._person_present = present
            if present:
                self._state.attention = min(1.0, self._state.attention + 0.4)

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_person_detected(self, ev) -> None:
        with self._lock:
            self._person_present = True
            self._state.attention = min(1.0, self._state.attention + 0.05)

    def _on_person_lost(self, ev) -> None:
        with self._lock:
            self._person_present = False
            self._state.attention = max(0.0, self._state.attention - 0.3)

    def _on_tts(self, ev) -> None:
        with self._lock:
            self._state.energy = max(0.0,
                self._state.energy - self._ENERGY_COST_SPEECH)
            self._state.social_drive = max(0.0,
                self._state.social_drive - self._SOCIAL_DECAY_INTERACT)
            self._state.interaction_fatigue = min(1.0,
                self._state.interaction_fatigue + self._FATIGUE_PER_TURN)

    def _on_command(self, ev) -> None:
        with self._lock:
            self._state.social_drive = max(0.0,
                self._state.social_drive - self._SOCIAL_DECAY_INTERACT * 0.5)
            self._state.interaction_fatigue = min(1.0,
                self._state.interaction_fatigue + self._FATIGUE_PER_TURN * 0.5)

    def _on_novel_event(self, ev) -> None:
        with self._lock:
            self._state.curiosity = min(1.0,
                self._state.curiosity + self._CURIOSITY_SPIKE * 0.3)

    # ── tick loop ─────────────────────────────────────────────────────────────

    def _tick_loop(self) -> None:
        while self._active:
            time.sleep(1.0)
            with self._lock:
                self._tick()

    def _tick(self) -> None:
        s = self._state
        mode = self._mm.current

        # Energy recovers at rest; night_watch mode is restful
        rest_multiplier = 1.5 if mode is Mode.NIGHT_WATCH else 1.0
        s.energy = min(1.0, s.energy + self._ENERGY_RECOVERY * rest_multiplier)

        # Social drive grows when alone
        if not self._person_present:
            s.social_drive = min(1.0, s.social_drive + self._SOCIAL_GROWTH_ALONE)

        # Curiosity decays slowly
        s.curiosity = max(0.0, s.curiosity - self._CURIOSITY_DECAY)

        # Attention decays
        s.attention = max(0.0, s.attention - self._ATTENTION_DECAY)

        # Fatigue recovers slowly
        s.interaction_fatigue = max(0.0,
            s.interaction_fatigue - self._FATIGUE_RECOVERY)

    # ── serialise for dashboard ───────────────────────────────────────────────

    def to_dict(self) -> dict:
        s = self.state
        return {
            "energy":              round(s.energy, 3),
            "social_drive":        round(s.social_drive, 3),
            "curiosity":           round(s.curiosity, 3),
            "attention":           round(s.attention, 3),
            "interaction_fatigue": round(s.interaction_fatigue, 3),
            "mood":                self.mood_label(),
            "energy_label":        self.energy_label(),
            "should_speak":        self.should_speak(),
            "should_explore":      self.should_explore(),
        }

    def __repr__(self) -> str:
        return f"<Conscience {self._state}>"
