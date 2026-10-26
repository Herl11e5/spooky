"""Startle: a bang is not a voice, its own sounds are its own, and it gets used to things."""

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bus import EventBus, EventType
from core.modes import Mode
from services.audio import LoudnessWatch
from skills.startle_skill import Habituation, StartleSkill

RATE = 16000
rng = np.random.default_rng(7)


def room(seconds=0.5, dbfs=-42.0):
    return rng.normal(0, 32768 * 10 ** (dbfs / 20), int(RATE * seconds))


def settled():
    w = LoudnessWatch()
    for _ in range(10):
        assert w.feed(room().astype(np.int16)) is None
    return w


def test_a_clap_is_a_bang():
    chunk = room()
    chunk[4000:4400] += rng.normal(0, 12000, 400) * np.exp(-np.arange(400) / 80)   # sharp, decaying
    bang = settled().feed(np.clip(chunk, -32768, 32767).astype(np.int16))
    assert bang and bang["db"] > -18 and bang["rise_db"] >= 15


def test_a_raised_voice_swells_and_is_not_a_bang():
    chunk = room()
    t = np.arange(8000) / RATE
    swell = np.clip(t / 0.15, 0, 1) * 9000 * np.sin(2 * np.pi * 180 * t)            # 150 ms onset
    assert settled().feed(np.clip(chunk + swell, -32768, 32767).astype(np.int16)) is None


def test_the_room_level_is_learnt_not_assumed():
    w = settled()
    assert -45 < w.room_db < -39


def test_habituation_halves_and_fades():
    h = Habituation(fade_s=900)
    assert [round(h.strength("sound", t), 2) for t in (0, 1, 2)] == [1.0, 0.5, 0.25]
    assert h.strength("sound", 2 + 5400) > 0.9                # an hour and a half of quiet: fresh
    assert h.strength("dark", 4000) == 1.0                    # another kind is news


def test_a_place_full_of_bangs_stops_startling_it():
    h = Habituation(fade_s=900)
    every_3_min = [h.strength("sound", t) for t in range(0, 3600, 180)]
    assert every_3_min[0] == 1.0 and max(every_3_min[3:]) < 0.3     # used to it within ten minutes


class Modes:
    current = Mode.COMPANION_DAY


class Audio:
    def __init__(self):
        self.said = []

    def say(self, text, feeling=None, wait=False):
        self.said.append(text)


class Motor:
    def __init__(self):
        self.moves = []

    def flinch(self):
        self.moves.append("flinch")

    def pan_head(self):
        self.moves.append("look_around")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def skill(state=None):
    bus, audio, motor, clock = EventBus(), Audio(), Motor(), Clock()
    s = StartleSkill(bus, Modes(), audio, motor, (lambda: state) if state is not None else None, clock)
    return s, bus, audio, motor, clock


def test_a_bang_flinches_then_a_cry_then_nothing():
    s, _, audio, motor, clock = skill()
    assert s.react("sound", "bang") == "flinch"
    assert motor.moves == ["flinch", "look_around"] and audio.said == ["Whoa! What was that?"]
    clock.t += 20
    assert s.react("sound", "bang") == "cry"
    clock.t += 20
    assert s.react("sound", "bang") is None


def test_while_talking_a_loud_word_is_not_a_startle():
    s, _, audio, motor, _ = skill()
    s._on_talk(None)
    assert s.react("sound", "loud") is None and not motor.moves


def test_night_watch_and_focus_keep_still_and_quiet():
    for mode in (Mode.NIGHT_WATCH, Mode.FOCUS_ASSISTANT):
        s, _, audio, motor, _ = skill()
        s._mm = type("M", (), {"current": mode})()
        assert s.react("sound", "bang") is None and not audio.said and not motor.moves


def test_the_body_stimulus_becomes_motion_and_a_reaction():
    state = {"stimulus": {"seq": 4, "kind": "looming", "detail": "0.55 → 0.38 m", "age_s": 9.0}}
    s, bus, _, _, _ = skill(state)
    seen = []
    bus.subscribe(EventType.MOTION_DETECTED, lambda ev: seen.append(ev.payload))
    bus.start()
    s.poll_body()                                             # from before it started: not news
    state["stimulus"] = {"seq": 5, "kind": "looming", "detail": "0.50 → 0.30 m", "age_s": 0.1}
    s.poll_body()
    for _ in range(50):
        if seen:
            break
        time.sleep(0.01)
    bus.stop()
    assert len(seen) == 1 and seen[0]["kind"] == "looming"
    assert s._pending.get_nowait() == ("looming", "0.50 → 0.30 m")
