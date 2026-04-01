"""
services/emotion.py — Express emotion through coordinated motion and voice.

Emotion is the physical manifestation of mood. When Spooky feels happy,
it dances and chirps. When wary, it steps back and speaks carefully.

This service listens to mood changes from PersonalityService and
coordinates MotorService + AudioService to create expressive behaviors.

Choreography patterns:
  - happy:    wiggle_dance, playful spin, antennae twitch
  - curious:  slow head pan, tilt up (ears forward), bright beep
  - playful:  mini-hop, unpredictable movements, chirping
  - wary:     step back, crouch (low profile), cautious speech
  - tired:    slow movements, extended yawns, drowsy voice
  - bored:    listless wandering, heavy sighs
  - content:  gentle rocking, periodic head adjustments
"""

from __future__ import annotations

import logging
import random
import threading
import time
from enum import Enum
from typing import Dict, Optional

from core.bus import EventBus, EventType
from services.personality import Mood

log = logging.getLogger(__name__)


class EmotionService:
    """
    Watches mood changes and expresses them via coordinated motion + voice.
    Connects PersonalityService (what to feel) with MotorService (how to move)
    and AudioService (what sound to make).
    """

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._lock = threading.RLock()
        self._active = False
        self._current_mood = Mood.CONTENT
        self._mood_start_time = time.time()
        self._last_expression_time = 0.0

        # Rate-limit expressions to avoid spamming motors
        self._MIN_EXPRESSION_INTERVAL = 2.0  # seconds

        # Reference to motor and audio services (set later via set_services)
        self._motor_service: Optional[object] = None
        self._audio_service: Optional[object] = None

        bus.subscribe(EventType.PERSONALITY_MOOD_CHANGED, self._on_mood_changed)

    def set_services(self, motor_service: object, audio_service: object) -> None:
        """Called by main.py to inject motor and audio service references."""
        self._motor_service = motor_service
        self._audio_service = audio_service

    def start(self) -> None:
        self._active = True
        log.info("EmotionService started")

    def stop(self) -> None:
        self._active = False
        log.info("EmotionService stopped")

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_mood_changed(self, event_data: Dict) -> None:
        """
        Triggered when Personality.current_mood changes significantly.
        Called by PersonalityService via bus event.
        """
        new_mood = event_data.get("mood", Mood.CONTENT)
        intensity = event_data.get("intensity", 0.5)

        with self._lock:
            self._current_mood = new_mood
            self._mood_start_time = time.time()

        # Express the emotion if it's strong enough
        if intensity > 0.3:
            self._express_emotion(new_mood, intensity)

    def _express_emotion(self, mood: Mood, intensity: float = 0.5) -> None:
        """Coordinate motor + audio to express an emotion."""
        now = time.time()
        if now - self._last_expression_time < self._MIN_EXPRESSION_INTERVAL:
            return  # Rate limit

        self._last_expression_time = now

        try:
            if mood == Mood.HAPPY:
                self._express_happy(intensity)
            elif mood == Mood.CURIOUS:
                self._express_curious(intensity)
            elif mood == Mood.PLAYFUL:
                self._express_playful(intensity)
            elif mood == Mood.WARY:
                self._express_wary(intensity)
            elif mood == Mood.TIRED:
                self._express_tired(intensity)
            elif mood == Mood.BORED:
                self._express_bored(intensity)
            elif mood == Mood.CONTENT:
                self._express_content(intensity)
        except Exception as e:
            log.error(f"Error expressing emotion {mood.value}: {e}")

    # ── emotion expressions ───────────────────────────────────────────────────

    def _express_happy(self, intensity: float) -> None:
        """Wiggle, spin, chirp enthusiastically."""
        if not self._motor_service or not self._audio_service:
            return

        try:
            # Wiggle dance
            for _ in range(int(2 * intensity)):
                self._motor_service.wiggle_dance()
                time.sleep(0.3)

            # Antennae twitch
            if hasattr(self._motor_service, "twitch_antennae"):
                self._motor_service.twitch_antennae()

            # Happy chirps
            chirps = int(3 * intensity)
            for _ in range(chirps):
                self._audio_service.play_sound("chirp_happy")
                time.sleep(0.2)

            log.info(f"Expressed happy (intensity {intensity:.2f})")
        except Exception as e:
            log.debug(f"Could not express happy: {e}")

    def _express_curious(self, intensity: float) -> None:
        """Slow head pan, tilt up, bright beep."""
        if not self._motor_service or not self._audio_service:
            return

        try:
            # Slow head pan (look around)
            if hasattr(self._motor_service, "pan_head"):
                self._motor_service.pan_head(speed=10)  # Slow
                time.sleep(1.5)
                self._motor_service.pan_head(0)  # Center

            # Tilt up (ears forward)
            if hasattr(self._motor_service, "tilt_head"):
                self._motor_service.tilt_head(20)

            # Curious chirp
            self._audio_service.play_sound("chirp_curious")

            log.info(f"Expressed curious (intensity {intensity:.2f})")
        except Exception as e:
            log.debug(f"Could not express curious: {e}")

    def _express_playful(self, intensity: float) -> None:
        """Mini-hop, unpredictable movements, chirping."""
        if not self._motor_service or not self._audio_service:
            return

        try:
            hops = int(2 * intensity)
            for _ in range(hops):
                if hasattr(self._motor_service, "hop"):
                    self._motor_service.hop()
                time.sleep(0.4)

            # Spin in random direction
            if hasattr(self._motor_service, "turn"):
                spin_degrees = random.choice([45, -45, 90, -90])
                self._motor_service.turn(spin_degrees)

            # Playful sounds
            for _ in range(int(2 * intensity)):
                self._audio_service.play_sound("chirp_play")
                time.sleep(0.25)

            log.info(f"Expressed playful (intensity {intensity:.2f})")
        except Exception as e:
            log.debug(f"Could not express playful: {e}")

    def _express_wary(self, intensity: float) -> None:
        """Step back, crouch, cautious voice."""
        if not self._motor_service or not self._audio_service:
            return

        try:
            # Step back
            if hasattr(self._motor_service, "move_backward"):
                self._motor_service.move_backward(distance=20, speed=30)

            # Crouch (low profile)
            if hasattr(self._motor_service, "crouch"):
                self._motor_service.crouch()

            # Wary beep (slow, low tone)
            self._audio_service.play_sound("beep_wary")

            log.info(f"Expressed wary (intensity {intensity:.2f})")
        except Exception as e:
            log.debug(f"Could not express wary: {e}")

    def _express_tired(self, intensity: float) -> None:
        """Slow movements, yawn, drowsy voice."""
        if not self._motor_service or not self._audio_service:
            return

        try:
            # Slow head tilt down
            if hasattr(self._motor_service, "tilt_head"):
                self._motor_service.tilt_head(-30)
                time.sleep(0.5)
                self._motor_service.tilt_head(0)

            # Yawn
            if hasattr(self._motor_service, "yawn"):
                self._motor_service.yawn()

            # Drowsy sound
            self._audio_service.play_sound("sigh_tired")

            log.info(f"Expressed tired (intensity {intensity:.2f})")
        except Exception as e:
            log.debug(f"Could not express tired: {e}")

    def _express_bored(self, intensity: float) -> None:
        """Listless wandering, heavy sighs."""
        if not self._motor_service or not self._audio_service:
            return

        try:
            # Slow wandering
            if hasattr(self._motor_service, "move_forward"):
                self._motor_service.move_forward(distance=10, speed=20)
                time.sleep(1.0)
                self._motor_service.move_backward(distance=10, speed=20)

            # Bored sigh
            self._audio_service.play_sound("sigh_bored")

            log.info(f"Expressed bored (intensity {intensity:.2f})")
        except Exception as e:
            log.debug(f"Could not express bored: {e}")

    def _express_content(self, intensity: float) -> None:
        """Gentle rocking, periodic head adjustments."""
        if not self._motor_service:
            return

        try:
            # Gentle rock forward/back
            if hasattr(self._motor_service, "rock"):
                self._motor_service.rock(amplitude=5, speed=30)

            log.info(f"Expressed content (intensity {intensity:.2f})")
        except Exception as e:
            log.debug(f"Could not express content: {e}")

    # ── utilities ─────────────────────────────────────────────────────────────

    def current_mood(self) -> Mood:
        with self._lock:
            return self._current_mood

    def mood_age(self) -> float:
        """How long (seconds) robot has been in current mood."""
        with self._lock:
            return time.time() - self._mood_start_time
