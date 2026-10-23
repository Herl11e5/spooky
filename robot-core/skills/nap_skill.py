"""
skills/nap_skill.py — A nap when nobody is around; a known face wakes it.

microduck's brain has a nap cycle. Here: standing, with nobody seen and nobody
talking to it for `nap_after_s`, spooky says it is sleepy and sits down — the body
turns the servos off, sparing battery and wear. When someone it knows comes back,
or calls it, it stands up again and says hello. Sitting down and standing up are
the brain's requests, so they happen only while the dashboard lets it move; if
the body refuses, it simply stays as it is. The body has its own backstop: it sits
down by itself after 15 minutes with nothing to do (robot.yaml → power.idle_sit_s).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager

log = logging.getLogger(__name__)


class NapSkill:

    def __init__(self, bus: EventBus, mode_manager: ModeManager, audio, motor,
                 state_fn: Callable[[], dict], nap_after_s: float = 300.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._mm = mode_manager
        self._audio = audio
        self._motor = motor
        self._state_fn = state_fn
        self._nap_after = nap_after_s
        self._clock = clock
        self._last_company = clock()
        self.napping = False
        self._stop = threading.Event()
        bus.subscribe(EventType.PERSON_DETECTED, self._on_someone)
        bus.subscribe(EventType.COMMAND_PARSED, self._on_someone)
        bus.subscribe(EventType.PERSON_IDENTIFIED, self._on_known)
        bus.subscribe(EventType.WAKE_WORD_DETECTED, self._on_called)

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="Nap").start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(15.0):
            self.check()

    # ── events ────────────────────────────────────────────────────────────────

    def _on_someone(self, ev) -> None:
        self._last_company = self._clock()

    def _on_known(self, ev) -> None:
        self._last_company = self._clock()
        self._wake_up(ev.get("display_name") or ev.get("person_id"))

    def _on_called(self, ev) -> None:
        self._last_company = self._clock()
        self._wake_up(None)

    # ── decisions ─────────────────────────────────────────────────────────────

    def check(self) -> None:
        if self.napping or self._mm.current not in (Mode.COMPANION_DAY, Mode.IDLE_OBSERVER):
            return
        if (self._state_fn() or {}).get("mode") not in ("active", "gesture"):
            return                                   # already sitting
        if self._clock() - self._last_company < self._nap_after:
            return
        self._last_company = self._clock()           # ask again only after another while
        if self._motor.sit():
            self.napping = True
            log.info("Nap: nobody around — sitting down")
            self._audio.say("I'm getting sleepy. Time for a little nap.", feeling="sleepy")

    def _wake_up(self, name) -> None:
        if not self.napping:
            return
        self.napping = False
        if self._motor.stand():
            log.info(f"Nap: woken by {name or 'a call'}")
            self._audio.say(f"Oh, {name}! I was having a nap." if name else "I'm awake, I'm awake!",
                            feeling="happy")
