"""
skills/empathy_skill.py — Reacting to how people look.

The body reads the expression of the face in front of it (robot-emulator:
robot/perception/faces.py); VisionService publishes EXPRESSION_CHANGED once a new
expression has lasted over a second. This skill answers in kind:

  happy      shares it: a bright chirp, a little dance
  sad        comes closer in spirit: a soft word, a slow nod, and gentler replies after
  angry      backs off: stops, and stays quiet for ten minutes
  surprised  is curious: what happened?
  fearful    reassures, and stops moving
  disgust    wonders if it was its fault

Reactions are quick canned lines, not a model call, because a reaction to a smile
has to come within a second. Each has its own cooldown per person, with a short
gap between any two, and it gets used to the faces someone makes often
(core/habituation.py, a half-hour half-life): on 2026-10-26 the owner's working
face read as "surprised" all afternoon, and the cooldowns alone let 22 "Did
something surprise you?" through in four and a half hours (44 remarks in all).
With habituation the same afternoon gives 17 remarks, 5 of them on surprise. Only in companion and
observer modes; never during night watch or focus. Movements go through the body,
which refuses them while the dashboard does not allow the brain to move — the
voice reaction happens anyway.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

from core.bus import EventBus, EventType
from core.habituation import Habituation
from core.modes import Mode, ModeManager

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Reaction:
    cooldown_s: float
    feeling: str                       # droid voice tone
    lines: Tuple[str, ...]
    gesture: Optional[str] = None      # a PicrawlerMotorService method


REACTIONS: Dict[str, Reaction] = {
    "happy": Reaction(300, "happy", (
        "You look happy! That makes my legs tingle.",
        "Nice smile. I like it when you smile.",
        "Somebody is in a good mood!"), "wiggle_dance"),
    "sad": Reaction(600, "sad", (
        "You look a little down. I'm right here if you want to talk.",
        "Rough day? I can keep you company.",
        "Hey. Whatever it is, I'm on your side."), "nod"),
    "angry": Reaction(600, "sleepy", (
        "Okay. I'll give you some space.",
        "I'll be quiet for a while."), None),
    "surprised": Reaction(120, "question", (
        "Whoa! What happened?",
        "Did something surprise you?"), "spin_head"),
    "fearful": Reaction(300, "sleepy", (
        "It's okay, it's just me.",
        "Don't worry. I'm harmless, mostly."), None),
    "disgust": Reaction(600, "question", (
        "Hmm. Was it something I did?",), None),
}

_QUIET_S = 600          # after an angry look
_GAP_S = 20             # between any two reactions
_NEWS = 0.7             # a face reacted to recently is not news below this


class EmpathySkill:

    def __init__(self, bus: EventBus, mode_manager: ModeManager, audio, motor=None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._bus = bus
        self._mm = mode_manager
        self._audio = audio
        self._motor = motor
        self._clock = clock
        self._last: Dict[Tuple[str, str], float] = {}
        self._habit = Habituation(fade_s=1800.0)       # (who, expression) → how used to it
        self._last_any = -1e9
        self._quiet_until = 0.0
        self._gentle_until = 0.0
        self._quieted = False             # True while focus mode is ours, not the user's
        self._active = False
        bus.subscribe(EventType.EXPRESSION_CHANGED, self._on_expression)
        bus.subscribe(EventType.MODE_CHANGED, self._on_mode)

    def start(self) -> None:
        self._active = True

    def stop(self) -> None:
        self._active = False

    @property
    def mood_of_company(self) -> Optional[str]:
        """'sad' for a while after a sad look — the mind answers more gently."""
        return "sad" if self._clock() < self._gentle_until else None

    def _on_mode(self, ev) -> None:
        if "empathy" not in str(ev.get("reason", "")):
            self._quieted = False             # someone else changed the mode: leave it be

    def _on_expression(self, ev) -> None:
        if not self._active:
            return
        expression = ev.get("expression")
        reaction = REACTIONS.get(expression)
        if reaction is None or self._mm.current not in (Mode.COMPANION_DAY, Mode.IDLE_OBSERVER):
            return
        now = self._clock()
        who = ev.get("person_id") or f"track{ev.get('track_id')}"
        if now - self._last_any < _GAP_S or now - self._last.get((who, expression), -1e9) < reaction.cooldown_s:
            return
        if now < self._quiet_until and expression != "fearful":
            return
        if self._habit.response((who, expression), now) < _NEWS:
            log.debug(f"Empathy: {who} looks {expression} again — used to it")
            return
        self._habit.note((who, expression), now)
        self._last[(who, expression)] = now
        self._last_any = now
        threading.Thread(target=self._react, args=(expression, reaction, ev.get("name")),
                         daemon=True, name="Empathy").start()

    def _react(self, expression: str, reaction: Reaction, name: Optional[str]) -> None:
        line = random.choice(reaction.lines)
        if name and expression in ("happy", "sad") and random.random() < 0.5:
            line = f"{name}, {line[0].lower()}{line[1:]}"
        log.info(f"Empathy: {name or 'someone'} looks {expression} → {line!r}")
        if expression in ("angry", "fearful") and self._motor is not None:
            self._motor.stop()                         # no moving around someone upset
        if expression == "angry":
            self._quiet_until = self._clock() + _QUIET_S
            if self._mm.current == Mode.COMPANION_DAY:
                self._mm.request_transition(Mode.FOCUS_ASSISTANT, reason="empathy: give space")
                self._quieted = True
                timer = threading.Timer(_QUIET_S, self._back_from_quiet)
                timer.daemon = True               # never holds up a shutdown
                timer.start()
        if expression == "sad":
            self._gentle_until = self._clock() + 900
        self._audio.say(line, feeling=reaction.feeling)
        if reaction.gesture and self._motor is not None:
            try:
                getattr(self._motor, reaction.gesture)()
            except Exception as exc:
                log.debug(f"Empathy gesture {reaction.gesture}: {exc}")
        self._bus.publish(EventType.SKILL_STARTED,
                          {"skill": "empathy", "expression": expression, "name": name or ""},
                          source="EmpathySkill")

    def _back_from_quiet(self) -> None:
        if self._quieted and self._mm.current == Mode.FOCUS_ASSISTANT:
            self._mm.request_transition(Mode.COMPANION_DAY, reason="empathy: quiet time over")
        self._quieted = False
