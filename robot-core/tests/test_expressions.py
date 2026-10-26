"""Expressions against a person's own resting face: a sad face that reads neutral is still sad."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.expressions import ExpressionBaseline

# The owner's resting face as the robot measured it (2026-10-26): close to pure neutral.
RESTING = {"angry": .0004, "disgust": .002, "fearful": 0.0, "happy": .017, "neutral": .959, "sad": .004, "surprised": .012}
SAD = {**RESTING, "neutral": .55, "sad": .42}           # clearly sadder — and still "neutral" for the model
A_BIT = {**RESTING, "neutral": .70, "disgust": .28}     # the most his face wobbled while he worked and talked
LAUGH = {**RESTING, "neutral": .05, "happy": .90}


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def warmed(tmp_path, seconds=200):
    clock = Clock()
    b = ExpressionBaseline(tmp_path, clock=clock)
    for _ in range(int(seconds / 0.5)):
        b.judge("p1", RESTING)
        clock.t += 0.5
    return b, clock


def test_nothing_is_inferred_before_the_person_has_been_watched(tmp_path):
    b, _ = warmed(tmp_path, seconds=60)
    assert b.judge("p1", SAD) is None


def test_sad_above_their_own_usual_counts_even_when_the_model_says_neutral(tmp_path):
    b, _ = warmed(tmp_path)
    assert b.judge("p1", SAD) == ("sad", 0.42, 0.0)
    assert b.judge("p1", A_BIT) is None


def test_a_minute_of_laughing_does_not_become_the_usual(tmp_path):
    b, clock = warmed(tmp_path)
    for _ in range(120):
        b.judge("p1", LAUGH)
        clock.t += 0.5
    assert b.usual("p1")["happy"] < 0.1
    assert b.judge("p1", SAD)[0] == "sad"


def test_the_usual_face_is_remembered_across_a_restart(tmp_path):
    b, _ = warmed(tmp_path)
    again = ExpressionBaseline(tmp_path, clock=Clock())
    assert abs(again.usual("p1")["neutral"] - 0.959) < 0.01
    assert again.judge("p1", SAD)[0] == "sad"


def test_forgetting_a_person_forgets_their_usual_face(tmp_path):
    b, _ = warmed(tmp_path)
    (tmp_path / "p1" / "expression_baseline.json").unlink()      # the face folder is deleted elsewhere
    b.forget("p1")
    assert b.usual("p1") is None
