"""
skills/explore_skill.py — Active exploration driven by curiosity.

When Spooky's curiosity is high and no person is nearby, this skill
engages active exploration of the environment.

Behaviors:
  - patrol_desk:    move around the desk perimeter
  - investigate:    approach interesting objects
  - pan_and_focus:  scan for movement/color, focus on it
  - smell_air:      emit a curious chirp while looking around

Activated by:
  - MindService when Conscience.curiosity > 0.65
  - PersonalityService.should_be_curious() = True

Terminates when:
  - PERSON_DETECTED event
  - Curiosity drops below threshold
  - Mode changes to non-explore mode
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


class ExploreSkill(Skill):
    """
    Autonomously explore environment driven by curiosity.
    Combines movement, sensing, and investigation behaviors.
    """

    name = "explore"
    allowed_modes = frozenset({Mode.COMPANION_DAY, Mode.IDLE_OBSERVER, Mode.FOCUS_ASSISTANT})

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
        self._person_nearby = False
        self._curiosity_level = 0.7  # Default

        bus.subscribe(EventType.PERSON_DETECTED, self._on_person)
        bus.subscribe(EventType.PERSON_LOST, self._on_person_lost)
        bus.subscribe(EventType.SCENE_ANALYZED, self._on_novelty)
        bus.subscribe(EventType.MODE_CHANGED, self._on_mode)

    # ── skill body ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        log.info("ExploreSkill: started")
        explore_duration = random.uniform(45, 120)  # 45–120 seconds
        start_time = time.time()

        while not self.should_stop:
            elapsed = time.time() - start_time

            if elapsed > explore_duration:
                log.info("ExploreSkill: exploration duration expired")
                break

            if self._person_nearby:
                log.info("ExploreSkill: person nearby, returning to idle")
                break

            behavior = self._choose_behavior()
            log.info(f"ExploreSkill: executing {behavior}")

            try:
                if behavior == "patrol":
                    self._patrol_desk()
                elif behavior == "investigate":
                    self._investigate_area()
                elif behavior == "pan_scan":
                    self._pan_and_scan()
                elif behavior == "smell_air":
                    self._smell_air()
                elif behavior == "climb_desk":
                    self._climb_desk()
            except Exception as e:
                log.error(f"ExploreSkill behavior error: {e}")

            # Brief rest between behaviors
            if not self.sleep(random.uniform(1.0, 3.0)):
                break

        log.info("ExploreSkill: terminated")

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_person(self, event_data: dict) -> None:
        self._person_nearby = True
        self.stop()

    def _on_person_lost(self, event_data: dict) -> None:
        self._person_nearby = False

    def _on_novelty(self, event_data: dict) -> None:
        """Scene analysis detected something interesting."""
        self._curiosity_level = min(1.0, self._curiosity_level + 0.2)

    def _on_mode(self, event_data: dict) -> None:
        new_mode = event_data.get("mode")
        if new_mode not in self.allowed_modes:
            self.stop()

    # ── exploration behaviors ─────────────────────────────────────────────────

    def _patrol_desk(self) -> None:
        """Move around desk perimeter."""
        if self._safety.is_obstacle_blocked:
            return

        if hasattr(self._motor, "move_forward"):
            self._motor.move_forward(distance=40, speed=45)
            self.sleep(0.5)

        if hasattr(self._motor, "turn"):
            self._motor.turn(90)
            self.sleep(0.3)
            self._motor.move_forward(distance=30, speed=40)
            self.sleep(0.5)

    def _investigate_area(self) -> None:
        """Approach and examine a location."""
        # Move forward slowly
        if hasattr(self._motor, "move_forward"):
            self._motor.move_forward(distance=25, speed=30)
            self.sleep(1.0)

        # Look around
        if hasattr(self._motor, "pan_head"):
            self._motor.pan_head(speed=20)
            self.sleep(1.5)
            self._motor.pan_head(0)

        # Investigate chirp
        self._audio.play_sound("chirp_curious")
        self.sleep(0.5)

    def _pan_and_scan(self) -> None:
        """Scan head across environment."""
        if not hasattr(self._motor, "pan_head"):
            return

        for _ in range(2):
            self._motor.pan_head(speed=30)
            self.sleep(2.0)
            self._motor.pan_head(0)

        self._audio.play_sound("chirp_focused")

    def _smell_air(self) -> None:
        """Emit curious chirps while looking around."""
        if hasattr(self._motor, "tilt_head"):
            self._motor.tilt_head(10)  # Head up, sniffing

        for _ in range(2):
            self._audio.play_sound("chirp_curious")
            self.sleep(0.3)

        if hasattr(self._motor, "tilt_head"):
            self._motor.tilt_head(0)

    def _climb_desk(self) -> None:
        """Attempt to climb obstacle or navigate around it."""
        if not hasattr(self._motor, "climb"):
            # Fallback: try to navigate around
            if hasattr(self._motor, "turn"):
                self._motor.turn(random.choice([-45, 45]))
                self.sleep(0.5)
            return

        try:
            self._motor.climb()
            self._audio.play_sound("chirp_excited")
            self.sleep(1.0)
        except Exception as e:
            log.debug(f"Climb failed: {e}")

    def _choose_behavior(self) -> str:
        """Pick random exploration behavior."""
        behaviors = [
            "patrol",
            "investigate",
            "pan_scan",
            "smell_air",
            "patrol",  # double weight
            "investigate",  # double weight
        ]
        
        if self._curiosity_level > 0.8:
            behaviors.extend(["climb_desk"])

        return random.choice(behaviors)
