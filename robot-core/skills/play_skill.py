"""
skills/play_skill.py — Interactive play behavior.

Play is: "do something funny/unexpected, get a laugh or aha moment from the person."

Behaviors:
  - peek:        hide head briefly, then peek out (anticipation + reveal)
  - head_spin:   rapid head rotation
  - dance_duel:  mirror-copy simple movements the person makes
  - riddle:      ask a simple riddle or joke in Italian
  - toy_carry:   pick up a small object from desk and "show" it
  - fake_charge: pretend to charge at person, then back off (playful threat)

Activated when:
  - PersonalityService.should_be_playful() is True
  - MindService decides to initiate play
  - A person interacts with a playful prompt ("Spooky, gioca!")

Terminates when:
  - Timer expires (30–45 seconds)
  - PERSON_LOST event
  - Mode changes to non-play (e.g., NIGHT_WATCH)
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
from services.choreography import Choreography
from skills.base import Skill

log = logging.getLogger(__name__)


class PlaySkill(Skill):
    """
    Engages in playful interaction with a person.
    Combines movement, sound, and reversals to create entertaining moments.
    """

    name = "play"
    allowed_modes = frozenset({Mode.COMPANION_DAY, Mode.FOCUS_ASSISTANT})

    def __init__(
        self,
        bus: EventBus,
        mode_manager: ModeManager,
        safety: SafetyMonitor,
        motor: MotorService,
        audio: AudioService,
        choreography: Choreography,
    ):
        super().__init__(bus, mode_manager, safety)
        self._motor = motor
        self._audio = audio
        self._choreo = choreography
        self._person_present = False

        bus.subscribe(EventType.PERSON_DETECTED, self._on_person)
        bus.subscribe(EventType.PERSON_LOST, self._on_person_lost)
        bus.subscribe(EventType.MODE_CHANGED, self._on_mode)

    # ── skill body ────────────────────────────────────────────────────────────

    def _run(self) -> None:
        log.info("PlaySkill: started")
        play_duration = random.uniform(30, 45)
        start_time = time.time()

        while not self.should_stop:
            if time.time() - start_time > play_duration:
                log.info("PlaySkill: duration expired")
                break

            if not self._person_present:
                log.info("PlaySkill: person lost, terminating")
                break

            behavior = self._choose_behavior()
            log.info(f"PlaySkill: executing {behavior}")

            try:
                if behavior == "peek":
                    self._peek()
                elif behavior == "head_spin":
                    self._head_spin()
                elif behavior == "dance_move":
                    self._dance_move()
                elif behavior == "joke":
                    self._tell_joke()
                elif behavior == "fake_charge":
                    self._fake_charge()
                elif behavior == "wiggle":
                    self._wiggle_dance()
            except Exception as e:
                log.error(f"PlaySkill behavior error: {e}")

            # Rest between behaviors
            if not self.sleep(2.0):
                break

        log.info("PlaySkill: terminated")

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_person(self, event_data: dict) -> None:
        self._person_present = True

    def _on_person_lost(self, event_data: dict) -> None:
        self._person_present = False
        self.stop()

    def _on_mode(self, event_data: dict) -> None:
        new_mode = event_data.get("mode")
        if new_mode not in self.allowed_modes:
            self.stop()

    # ── behavior implementations ──────────────────────────────────────────────

    def _peek(self) -> None:
        """Hide head, then peek out with a chirp."""
        # Tilt head down
        if hasattr(self._motor, "tilt_head"):
            self._motor.tilt_head(-40)
            self.sleep(0.5)

        # Pause (anticipation)
        self.sleep(0.3)

        # Quickly back up
        if hasattr(self._motor, "tilt_head"):
            self._motor.tilt_head(10)
            self._audio.say("Ciao!")
            self.sleep(0.2)

    def _head_spin(self) -> None:
        """Spin head around rapidly."""
        if not hasattr(self._motor, "spin_head"):
            return

        for _ in range(2):
            if hasattr(self._motor, "spin_head"):
                self._motor.spin_head(speed=80)
                self.sleep(0.4)

        self._audio.play_sound("chirp_excited")

    def _dance_move(self) -> None:
        """Execute a fun dance sequence."""
        if hasattr(self._motor, "wiggle_dance"):
            self._motor.wiggle_dance()
            self._audio.play_sound("chirp_play")
            self.sleep(0.5)

        if hasattr(self._motor, "hop"):
            for _ in range(2):
                self._motor.hop()
                self.sleep(0.3)

    def _tell_joke(self) -> None:
        """Tell a simple joke or funny observation."""
        jokes = [
            "Sai qual è il ragno più veloce? Quello che ha i riflessi pronti! *clic clic*",
            "Perché i ragni non vanno mai al cinema? Perché preferiscono stare sul web!",
            "Quante zampe servono a un ragno per ballare? Tutte! *Si danno una spinnata*",
            "Mi sento un po' web-stanco oggi.",
            "Ho visto una mosca vicino. Per un attimo ho pensato fosse un'occasione!",
        ]
        joke = random.choice(jokes)
        self._audio.say(joke)
        self._motor.wiggle_dance()

    def _fake_charge(self) -> None:
        """Pretend to charge, then back off."""
        if hasattr(self._motor, "move_forward"):
            # Quick forward
            self._motor.move_forward(distance=30, speed=100)
            self._audio.play_sound("chirp_aggressive")
            self.sleep(0.3)

            # Quick backward
            self._motor.move_backward(distance=30, speed=100)
            self._audio.say("Scherzo!")
            self.sleep(0.2)

    def _wiggle_dance(self) -> None:
        """Simple wiggle."""
        if hasattr(self._motor, "wiggle_dance"):
            self._motor.wiggle_dance()
            self.sleep(0.5)

    def _choose_behavior(self) -> str:
        """Pick a random play behavior."""
        behaviors = [
            "peek",
            "head_spin",
            "dance_move",
            "joke",
            "fake_charge",
            "wiggle",
        ]
        return random.choice(behaviors)
