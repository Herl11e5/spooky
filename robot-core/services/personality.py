"""
services/personality.py — Personality traits and emotional mood system.

Personality is what makes Spooky unique. Unlike Conscience (which tracks
internal drives), Personality defines HOW Spooky responds and behaves.

TRAITS (0.0 – 1.0):
  - curiosity:    urge to explore and learn (high = asks lots of "why?")
  - friendliness: inclination to greet and interact warmly
  - mischief:     playful tendency to do unexpected things
  - loyalty:      how attached to specific people (familiarity-biased)

MOODS (current emotional state):
  - happy:        excited, responsive, motor active
  - curious:      focused, investigative
  - playful:      energetic, unpredictable
  - wary:         cautious, withdrawn, low energy
  - tired:        sleepy, minimal responses
  - bored:        indifferent, low engagement
  - content:      baseline neutral satisfaction

Mood evolves naturally over time based on interactions and events,
modulated by underlying traits. For example, a high-curiosity Spooky
enters "curious" mood faster and stays there longer.

This service stores personality state and provides methods for:
  - Querying current mood/traits
  - Modulating responses based on personality
  - Expressing emotion through motion and speech
"""

from __future__ import annotations

import logging
import math
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager

log = logging.getLogger(__name__)


class Mood(Enum):
    HAPPY = "happy"
    CURIOUS = "curious"
    PLAYFUL = "playful"
    WARY = "wary"
    TIRED = "tired"
    BORED = "bored"
    CONTENT = "content"


@dataclass
class PersonalityTraits:
    """Immutable snapshot of personality traits (0.0–1.0)."""
    curiosity: float = 0.7
    friendliness: float = 0.6
    mischief: float = 0.5
    loyalty: float = 0.8

    def __repr__(self) -> str:
        return (
            f"curious={self.curiosity:.2f} "
            f"friendly={self.friendliness:.2f} "
            f"mischief={self.mischief:.2f} "
            f"loyal={self.loyalty:.2f}"
        )


class PersonalityService:
    """
    Manages Spooky's personality traits and emotional mood.
    Runs a background tick to naturally evolve mood based on events.
    """

    # Mood transition rates (per second)
    _MOOD_DECAY = 0.005  # How fast mood returns to baseline (content)
    _HAPPY_FROM_INTERACTION = 0.15
    _CURIOUS_FROM_NOVELTY = 0.20
    _PLAYFUL_FROM_PLAY = 0.18
    _WARY_FROM_DANGER = 0.25
    _TIRED_FROM_ACTIVITY = 0.006
    _BORED_FROM_INACTIVITY = 0.004

    def __init__(self, bus: EventBus, mode_manager: ModeManager, traits: Optional[PersonalityTraits] = None):
        self._bus = bus
        self._mm = mode_manager
        self._traits = traits or PersonalityTraits()
        self._lock = threading.RLock()
        self._active = False
        self._thread: Optional[threading.Thread] = None

        # Mood state: intensity (0.0–1.0) for each mood
        self._mood_intensities: Dict[Mood, float] = {m: 0.0 for m in Mood}
        self._mood_intensities[Mood.CONTENT] = 1.0

        self._last_interaction_time = 0.0
        self._last_novelty_time = 0.0
        self._last_play_time = 0.0
        self._danger_detected = False
        self._active_time = 0.0  # Track how long robot has been active

        bus.subscribe(EventType.PERSON_DETECTED, self._on_person)
        bus.subscribe(EventType.PERSON_IDENTIFIED, self._on_person_identified)
        bus.subscribe(EventType.SCENE_ANALYZED, self._on_novelty)
        bus.subscribe(EventType.MOTION_DETECTED, self._on_novelty)
        bus.subscribe(EventType.SAFETY_FAULT, self._on_danger)
        bus.subscribe(EventType.COMMAND_PARSED, self._on_interaction)

    # ── lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        self._active = True
        self._thread = threading.Thread(
            target=self._mood_tick_loop, daemon=True, name="Personality"
        )
        self._thread.start()
        log.info(f"PersonalityService started with traits: {self._traits}")

    def stop(self) -> None:
        self._active = False
        if self._thread:
            self._thread.join(timeout=5.0)
        log.info("PersonalityService stopped")

    # ── public reads ──────────────────────────────────────────────────────────

    @property
    def traits(self) -> PersonalityTraits:
        with self._lock:
            return PersonalityTraits(
                curiosity=self._traits.curiosity,
                friendliness=self._traits.friendliness,
                mischief=self._traits.mischief,
                loyalty=self._traits.loyalty,
            )

    @property
    def current_mood(self) -> Mood:
        """Return the dominant mood right now."""
        with self._lock:
            dominant = max(self._mood_intensities.items(), key=lambda x: x[1])
            return dominant[0]

    def get_mood_intensity(self, mood: Mood) -> float:
        """Get intensity of a specific mood (0.0–1.0)."""
        with self._lock:
            return self._mood_intensities[mood]

    def mood_label(self) -> str:
        """Human-readable mood label."""
        mood = self.current_mood
        intensity = self.get_mood_intensity(mood)
        
        if intensity < 0.3:
            return "calm"
        elif intensity < 0.6:
            return mood.value
        else:
            return f"{mood.value}!"  # Exclamation for strong emotions

    def should_be_playful(self) -> bool:
        """True if personality + mood favor playful behavior."""
        playful_intensity = self.get_mood_intensity(Mood.PLAYFUL)
        return self._traits.mischief > 0.5 and playful_intensity > 0.3

    def should_be_curious(self) -> bool:
        """True if personality + mood favor exploration."""
        curious_intensity = self.get_mood_intensity(Mood.CURIOUS)
        return self._traits.curiosity > 0.6 and curious_intensity > 0.4

    def friendliness_multiplier(self) -> float:
        """0.0–2.0 multiplier on greeting enthusiam based on mood + traits."""
        happy_intensity = self.get_mood_intensity(Mood.HAPPY)
        wary_intensity = self.get_mood_intensity(Mood.WARY)
        base = self._traits.friendliness
        return base * (1.0 + happy_intensity - wary_intensity)

    def response_variance(self) -> float:
        """0.0–1.0 variance in responses. High = unpredictable/playful."""
        playful = self.get_mood_intensity(Mood.PLAYFUL)
        return self._traits.mischief + playful * 0.3

    def describe_mood(self) -> str:
        """Multi-line mood description for debugging."""
        with self._lock:
            lines = [f"Traits: {self._traits}"]
            lines.append(f"Current mood: {self.current_mood.value} (intensity {self.get_mood_intensity(self.current_mood):.2f})")
            sorted_moods = sorted(self._mood_intensities.items(), key=lambda x: -x[1])
            lines.append(f"Mood state: {', '.join(f'{m.value}={i:.2f}' for m, i in sorted_moods[:3])}")
            return "\n".join(lines)

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_person(self, event_data: Dict) -> None:
        with self._lock:
            self._raise_mood(Mood.HAPPY, 0.2)
            self._lower_mood(Mood.BORED, 0.3)
            self._last_interaction_time = time.time()

    def _on_person_identified(self, event_data: Dict) -> None:
        with self._lock:
            # Recognized person = extra happiness!
            self._raise_mood(Mood.HAPPY, 0.25)
            if "confidence" in event_data and event_data["confidence"] > 0.8:
                # High confidence recognition = extra joy
                self._raise_mood(Mood.PLAYFUL, 0.15)

    def _on_novelty(self, event_data: Dict) -> None:
        with self._lock:
            self._raise_mood(Mood.CURIOUS, 0.18)
            self._last_novelty_time = time.time()

    def _on_danger(self, event_data: Dict) -> None:
        with self._lock:
            self._raise_mood(Mood.WARY, 0.3)
            self._lower_mood(Mood.PLAYFUL, 0.4)
            self._danger_detected = True

    def _on_interaction(self, event_data: Dict) -> None:
        with self._lock:
            self._raise_mood(Mood.HAPPY, 0.2)
            self._active_time += 1.0

    # ── mood manipulation ─────────────────────────────────────────────────────

    def _raise_mood(self, mood: Mood, amount: float) -> None:
        """Increase a mood's intensity (clamped 0–1)."""
        current = self._mood_intensities[mood]
        self._mood_intensities[mood] = min(1.0, current + amount)

    def _lower_mood(self, mood: Mood, amount: float) -> None:
        """Decrease a mood's intensity (clamped 0–1)."""
        current = self._mood_intensities[mood]
        self._mood_intensities[mood] = max(0.0, current - amount)

    def _normalize_moods(self) -> None:
        """Ensure total intensity doesn't exceed bounds."""
        total = sum(self._mood_intensities.values())
        if total > 1.0:
            scale = 1.0 / total
            for m in self._mood_intensities:
                self._mood_intensities[m] *= scale

    # ── mood tick loop (runs every ~1 second) ─────────────────────────────────

    def _mood_tick_loop(self) -> None:
        """Background thread that naturally evolves mood over time."""
        while self._active:
            try:
                time.sleep(1.0)

                with self._lock:
                    # Decay all moods toward baseline (content)
                    for mood in Mood:
                        if mood != Mood.CONTENT:
                            current = self._mood_intensities[mood]
                            self._mood_intensities[mood] = max(0.0, current - self._MOOD_DECAY)

                    # Accumulate tiredness from activity
                    self._active_time += 1.0
                    if self._active_time > 600:  # 10 minutes
                        self._raise_mood(Mood.TIRED, self._TIRED_FROM_ACTIVITY)
                        self._active_time = 0.0

                    # Boredom from inactivity
                    time_since_interaction = time.time() - self._last_interaction_time
                    if time_since_interaction > 180:  # 3 minutes
                        self._raise_mood(Mood.BORED, self._BORED_FROM_INACTIVITY)

                    # Clear danger flag after 10 seconds
                    if self._danger_detected:
                        self._danger_detected = False

                    # Energy recovery from tiredness at rest
                    if self._mm.current == Mode.IDLE_OBSERVER:
                        self._lower_mood(Mood.TIRED, 0.01)

                    # Normalize so sum doesn't explode
                    self._normalize_moods()

            except Exception as e:
                log.error(f"Error in mood tick: {e}")
