"""
skills/startle_skill.py — Startled by what changes all at once, and getting used to it.

microduck's brain reacts to sudden changes. The changes are noticed next to their
sensors: a loud sound by the microphone (services/audio.py → UNUSUAL_SOUND),
something coming at the robot and the lights going off or on by the body
(robot-emulator's robot/perception/stimuli.py → `stimulus` in its state, which
this skill turns into MOTION_DETECTED). This is the one place that decides what
to do about them — before it, a clap would have set off three moves at once.

The reaction: a flinch (the body's "flinch" gesture: back and down, a moment
frozen) and a cry; when nobody is there, a look round after it. Then it gets used
to it, as an animal does: the same kind of thing again soon after gets only a
cry, a third gets nothing, until two quiet minutes have passed — a television
does not keep it jumping all evening.

Not while it is speaking or someone is talking to it (a raised voice is not a
bang). In focus mode it keeps quiet — focus means no initiative, and a "Hm?" at
every clap is one (2026-10-23). In night watch it stays still and silent too:
the events still reach the alerts (services/alert.py), which is where an
intruder belongs.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable, Optional

from core.bus import EventBus, EventType
from core.modes import Mode, ModeManager

log = logging.getLogger(__name__)

_CRIES = {
    "sound":   ("Whoa! What was that?", "Hm?"),
    "looming": ("Hey! Not so close!", "Hm."),
    "dark":    ("Hey, who turned off the lights?", "Dark again."),
    "bright":  ("Oh! Lights!", "Lights."),
}


class Habituation:
    """
    How strongly to react: halved by each stimulus of the same kind in the current
    bout, a bout being stimuli less than `recover_s` apart.
    """

    def __init__(self, recover_s: float = 120.0) -> None:
        self._recover = recover_s
        self._bout: dict[str, tuple[int, float]] = {}      # kind → (count, last)

    def strength(self, kind: str, now: float) -> float:
        count, last = self._bout.get(kind, (0, -1e9))
        if now - last >= self._recover:
            count = 0                                      # two quiet minutes: fresh again
        self._bout[kind] = (count + 1, now)
        return 0.5 ** count


class StartleSkill:

    POLL_S = 0.2

    def __init__(self, bus: EventBus, mode_manager: ModeManager, audio, motor,
                 state_fn: Optional[Callable[[], dict]] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._bus = bus
        self._mm = mode_manager
        self._audio = audio
        self._motor = motor
        self._state_fn = state_fn               # the body's state, None without a body
        self._clock = clock
        self._habit = Habituation()
        self._pending: "queue.Queue[tuple[str, str]]" = queue.Queue()
        self._speaking = False
        self._talking_until = 0.0
        self._someone_here = False
        self._seen_seq: Optional[int] = None
        self._stop = threading.Event()
        bus.subscribe(EventType.UNUSUAL_SOUND, lambda ev: self._pending.put(("sound", str(ev.payload))))
        bus.subscribe(EventType.TTS_STARTED, lambda ev: setattr(self, "_speaking", True))
        bus.subscribe(EventType.TTS_FINISHED, lambda ev: setattr(self, "_speaking", False))
        bus.subscribe(EventType.WAKE_WORD_DETECTED, self._on_talk)
        bus.subscribe(EventType.COMMAND_PARSED, self._on_talk)
        bus.subscribe(EventType.PERSON_DETECTED, lambda ev: setattr(self, "_someone_here", True))
        bus.subscribe(EventType.PERSON_LOST, lambda ev: setattr(self, "_someone_here", False))

    def start(self) -> None:
        threading.Thread(target=self._run, daemon=True, name="Startle").start()

    def stop(self) -> None:
        self._stop.set()

    def _on_talk(self, ev) -> None:
        self._talking_until = self._clock() + 8.0

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_body()
            try:
                kind, detail = self._pending.get(timeout=self.POLL_S)
            except queue.Empty:
                continue
            self.react(kind, detail)

    # ── the body's stimuli ────────────────────────────────────────────────────

    def poll_body(self) -> None:
        if self._state_fn is None:
            return
        s = (self._state_fn() or {}).get("stimulus")
        if not s:
            if self._seen_seq is None:
                self._seen_seq = 0
            return
        if self._seen_seq is None or s["seq"] == self._seen_seq:
            self._seen_seq = s["seq"]                    # one from before we started: not news
            return
        self._seen_seq = s["seq"]
        if s.get("age_s", 0.0) > 2.0:
            return
        self._bus.publish(EventType.MOTION_DETECTED, {"kind": s["kind"], "detail": s["detail"]},
                          source="StartleSkill")
        self._pending.put((s["kind"], s["detail"]))

    # ── the decision ──────────────────────────────────────────────────────────

    def react(self, kind: str, detail: str) -> Optional[str]:
        """What it did: "flinch", "cry", or None. Public so tests can drive it."""
        now = self._clock()
        mode = self._mm.current
        if mode in (Mode.NIGHT_WATCH, Mode.SAFE_SHUTDOWN, Mode.FOCUS_ASSISTANT):
            log.info(f"Startle: {kind} ({detail}) — keeping still ({mode.value})")
            return None
        if self._speaking or now < self._talking_until:
            log.debug(f"Startle: {kind} while talking — not a startle")
            return None
        strength = self._habit.strength(kind, now)
        if strength < 0.3:
            log.info(f"Startle: {kind} again — used to it by now")
            return None
        full, short = _CRIES.get(kind, ("Whoa!", "Hm?"))
        if strength < 0.9:
            self._audio.say(short, feeling="question")
            log.info(f"Startle: {kind} ({detail}) — a cry")
            return "cry"
        log.info(f"Startle: {kind} ({detail}) — flinch")
        self._audio.say(full, feeling="alert")          # at once; the flinch waits for its gesture
        flinch = getattr(self._motor, "flinch", None)
        if flinch is not None:
            flinch()
        if not self._someone_here and hasattr(self._motor, "pan_head"):
            self._motor.pan_head()                       # nobody here: look round for it
        return "flinch"
