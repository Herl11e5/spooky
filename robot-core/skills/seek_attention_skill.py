"""
skills/seek_attention_skill.py — Autonomously seek interaction with person.

When Spooky feels lonely (high social_drive) or playful (high mischief),
it will try to get the person's attention.

Behaviors:
  - nudge:       gently bump forward/backward
  - chirp_loud:  emit loud chirp sequence
  - blink_on:    cycle lights/LEDs rapidly if available
  - pat_desk:    tap desk surface with leg
  - wave:        lift antenna/leg as if waving

Escalation:
  - Level 1: Soft chirp + gentle movement
  - Level 2: Louder chirps + more active movement
  - Level 3: Aggressive attention-seeking (move around desk)
  - Give up after 30 seconds if person doesn't respond

Published event:
  - ATTENTION_SOUGHT (for MindService to decide response)
"""

from __future__ import annotations

import logging
import random
import time
from typing import Optional

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager
from core.safety import SafetyMonitor
from services.motor import MotorService
from services.audio import AudioService
from skills.base import Skill

log = logging.getLogger(__name__)


class SeekAttentionSkill(Skill):
    """
    Tries to get the person's attention through escalating behaviors.
    Used when Spooky feels bored or lonely.
    """

    name = "seek_attention"
    allowed_modes = frozenset({Mode.COMPANION_DAY, Mode.FOCUS_ASSISTANT})

    def __init__(
        self,
        bus: EventBus,
        mode_manager: ModeManager,
        safety: SafetyMonitor,
        motor: MotorService,
        audio: AudioService,
    ):
        super().__init__(bus, mode_manager, safety)
        self._motor = motor
        self._audio = audio
        self._attention_level = 1

        bus.subscribe(EventType.COMMAND_PARSED, self._on_person_responds)
        bus.subscribe(EventType.MODE_CHANGED, self._on_mode)

    # ── skill body ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        log.info("SeekAttentionSkill: started")
        start_time = time.time()
        max_duration = 30  # Give up after 30 seconds

        while not self.should_stop:
            elapsed = time.time() - start_time

            if elapsed > max_duration:
                log.info("SeekAttentionSkill: timeout, giving up")
                self._audio.say("Ok, continuo da solo.")
                break

            # Update attention level based on time
            if elapsed < 10:
                self._attention_level = 1
            elif elapsed < 20:
                self._attention_level = 2
            else:
                self._attention_level = 3

            log.info(f"SeekAttentionSkill: attention level {self._attention_level}")

            try:
                if self._attention_level == 1:
                    self._seek_level_1()
                elif self._attention_level == 2:
                    self._seek_level_2()
                else:
                    self._seek_level_3()
            except Exception as e:
                log.error(f"SeekAttentionSkill error: {e}")

            if not self.sleep(3.0):
                break

        log.info("SeekAttentionSkill: terminated")

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_person_responds(self, event_data: dict) -> None:
        """Person responded! Skill's work is done."""
        log.info("SeekAttentionSkill: person responded!")
        self._audio.say("Finalmente! Mi dicevi di fare?")
        self.stop()

    def _on_mode(self, event_data: dict) -> None:
        new_mode = event_data.get("mode")
        if new_mode not in self.allowed_modes:
            self.stop()

    # ── attention-seeking behaviors ───────────────────────────────────────────

    def _seek_level_1(self) -> None:
        """Soft, polite attention-seeking."""
        # Gentle nudge
        if hasattr(self._motor, "nudge"):
            self._motor.nudge()
        else:
            if hasattr(self._motor, "move_forward"):
                self._motor.move_forward(distance=10, speed=30)
                self.sleep(0.5)
                self._motor.move_backward(distance=10, speed=30)

        # Soft chirp
        self._audio.play_sound("chirp_curious")
        self.sleep(0.3)

    def _seek_level_2(self) -> None:
        """More insistent attention-seeking."""
        # Multiple chirps
        for _ in range(2):
            self._audio.play_sound("chirp_excited")
            self.sleep(0.25)

        # Wiggle dance
        if hasattr(self._motor, "wiggle_dance"):
            self._motor.wiggle_dance()

        # Pat desk
        if hasattr(self._motor, "pat_desk"):
            self._motor.pat_desk()

        self._audio.say("Pssst! Sono qui!")
        self.sleep(0.5)

    def _seek_level_3(self) -> None:
        """Desperate attention-seeking — move around and make noise."""
        # Move around
        if hasattr(self._motor, "move_forward"):
            self._motor.move_forward(distance=30, speed=60)
            self.sleep(0.3)
            self._motor.move_backward(distance=30, speed=60)

        # Loud chirp sequence
        for _ in range(3):
            self._audio.play_sound("chirp_aggressive")
            self.sleep(0.15)

        # Plea
        self._audio.say("Ehi! Guarda qua! Mi ascolta?")
        self.sleep(0.5)

        # One more dance for emphasis
        if hasattr(self._motor, "wiggle_dance"):
            for _ in range(2):
                self._motor.wiggle_dance()
                self.sleep(0.3)
