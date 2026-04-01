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

    # Different rate limits by expression type
    _MIN_INTERVAL_FULL   = 3.0   # full choreography (happy dance, wary retreat)
    _MIN_INTERVAL_MICRO  = 0.8   # micro-expressions (startle, fidget)
    _AMBIENT_INTERVAL    = 20.0  # spontaneous ambient micro-expressions

    def __init__(self, bus: EventBus):
        self._bus = bus
        self._lock = threading.RLock()
        self._active = False
        self._current_mood = Mood.CONTENT
        self._mood_start_time = time.time()
        self._last_full_expression_time = 0.0
        self._last_micro_expression_time = 0.0
        self._last_ambient_time = time.time()
        self._choreo: Optional[object] = None

        # Reference to motor and audio services (set later via set_services)
        self._motor_service: Optional[object] = None
        self._audio_service: Optional[object] = None

        bus.subscribe(EventType.PERSONALITY_MOOD_CHANGED, self._on_mood_changed)
        bus.subscribe(EventType.PICKED_UP,  self._on_picked_up)
        bus.subscribe(EventType.PUT_DOWN,   self._on_put_down)
        bus.subscribe(EventType.UNUSUAL_SOUND, self._on_startle)

    def set_services(self, motor_service: object, audio_service: object,
                     choreography: Optional[object] = None) -> None:
        """Called by main.py to inject motor, audio, and choreography references."""
        self._motor_service = motor_service
        self._audio_service = audio_service
        self._choreo = choreography

    def start(self) -> None:
        self._active = True
        # Ambient micro-expression thread
        threading.Thread(
            target=self._ambient_tick, daemon=True, name="EmotionAmbient"
        ).start()
        log.info("EmotionService started")

    def stop(self) -> None:
        self._active = False
        log.info("EmotionService stopped")

    # ── ambient tick ──────────────────────────────────────────────────────────

    def _ambient_tick(self) -> None:
        """Spontaneous micro-expressions even without explicit mood events."""
        while self._active:
            time.sleep(5.0)
            if not self._active:
                break
            now = time.time()
            if now - self._last_ambient_time < self._AMBIENT_INTERVAL:
                continue
            if not self._motor_service:
                continue
            # Pick a lightweight ambient micro-expression
            action = random.choice([
                "fidget", "head_tilt_left", "head_tilt_right",
                "curiosity_pan", None, None,   # None = skip
            ])
            if action and self._choreo:
                try:
                    self._choreo.play(action)
                    self._last_ambient_time = now
                except Exception:
                    pass

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_mood_changed(self, event_data: Dict) -> None:
        """Triggered when dominant mood changes (published by PersonalityService)."""
        new_mood = event_data.get("mood", Mood.CONTENT)
        intensity = event_data.get("intensity", 0.5)

        with self._lock:
            self._current_mood = new_mood
            self._mood_start_time = time.time()

        if intensity > 0.3:
            self._express_emotion(new_mood, intensity)

    def _on_picked_up(self, ev) -> None:
        """Immediate startle + scared reaction when lifted."""
        if self._choreo:
            try:
                self._choreo.play("picked_up_react")
            except Exception:
                pass
        if self._audio_service:
            try:
                self._audio_service.play_sound("chirp_curious")
            except Exception:
                pass
        self._last_micro_expression_time = time.time()

    def _on_put_down(self, ev) -> None:
        """Reorientation after being set down."""
        if self._choreo:
            try:
                self._choreo.play("put_down_react")
            except Exception:
                pass
        self._last_micro_expression_time = time.time()

    def _on_startle(self, ev) -> None:
        """React to unexpected sounds with a quick micro-expression."""
        now = time.time()
        if now - self._last_micro_expression_time < self._MIN_INTERVAL_MICRO:
            return
        self._last_micro_expression_time = now
        if self._choreo:
            try:
                self._choreo.play("startle")
            except Exception:
                pass

    def _express_emotion(self, mood: Mood, intensity: float = 0.5) -> None:
        """Coordinate motor + audio to express an emotion."""
        now = time.time()
        # Micro-expressions (surprised, curious) have a shorter rate limit
        is_micro = mood in (Mood.SURPRISED, Mood.CURIOUS)
        min_interval = self._MIN_INTERVAL_MICRO if is_micro else self._MIN_INTERVAL_FULL
        last = self._last_micro_expression_time if is_micro else self._last_full_expression_time
        if now - last < min_interval:
            return

        if is_micro:
            self._last_micro_expression_time = now
        else:
            self._last_full_expression_time = now

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
            elif mood == Mood.SURPRISED:
                self._express_surprised(intensity)
        except Exception as e:
            log.error(f"Error expressing emotion {mood.value}: {e}")

    # ── emotion expressions ───────────────────────────────────────────────────

    def _express_happy(self, intensity: float) -> None:
        """Wiggle, celebratory spin, chirp enthusiastically."""
        try:
            if self._choreo:
                self._choreo.play("excited" if intensity > 0.6 else "greeting")
            if self._motor_service:
                try:
                    self._motor_service.wiggle_dance()
                except Exception:
                    pass
            if self._audio_service:
                for _ in range(min(int(2 * intensity), 3)):
                    self._audio_service.play_sound("chirp_happy")
                    time.sleep(0.15)
            log.info(f"Expressed happy (intensity {intensity:.2f})")
        except Exception as e:
            log.debug(f"Could not express happy: {e}")

    def _express_curious(self, intensity: float) -> None:
        """Head tilt + investigate + curious chirp."""
        try:
            if self._choreo:
                tilt = random.choice(["head_tilt_left", "head_tilt_right", "investigate"])
                self._choreo.play(tilt)
            if self._audio_service:
                self._audio_service.play_sound("chirp_curious")
            log.info(f"Expressed curious (intensity {intensity:.2f})")
        except Exception as e:
            log.debug(f"Could not express curious: {e}")

    def _express_surprised(self, intensity: float) -> None:
        """Quick startle reaction — fast head snap + sound."""
        try:
            if self._choreo:
                self._choreo.play("startle")
            if self._audio_service:
                self._audio_service.play_sound("chirp_curious")
            log.info(f"Expressed surprised (intensity {intensity:.2f})")
        except Exception as e:
            log.debug(f"Could not express surprised: {e}")

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
        """Listless look-around, heavy sigh."""
        try:
            if self._choreo:
                self._choreo.play("bored_sigh")
            if self._audio_service:
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
