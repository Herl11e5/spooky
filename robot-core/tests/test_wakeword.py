"""The dedicated wake-word detector: the robot's features match openWakeWord's, the model loads, the tap is opt-in."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.wakeword import WINDOW, WakeFeatures, WakeModel

MODELS = Path(__file__).resolve().parents[1] / "models" / "wakeword"
rng = np.random.default_rng(3)


def test_one_frame_per_80_ms_whatever_the_piece_size():
    a, b = WakeFeatures(MODELS), WakeFeatures(MODELS)
    audio = rng.normal(0, 800, 16000 * 3).astype(np.int16)
    a.feed(audio)
    for i in range(0, len(audio), 999):                  # odd pieces, as a microphone gives them
        b.feed(audio[i:i + 999])
    assert len(a.frames) == len(b.frames) == 37 and np.allclose(a.frames, b.frames)


def test_the_classifier_scores_a_window_and_stays_quiet_on_noise(tmp_path):
    m = WakeModel(MODELS, tmp_path)
    scores = [m.feed(rng.normal(0, 300, 8000).astype(np.int16)) for _ in range(8)]
    assert all(s is None or 0.0 <= s < 0.5 for s in scores)


def test_the_room_tap_is_off_unless_asked_and_keeps_no_audio(tmp_path):
    t = [1000.0]
    m = WakeModel(MODELS, tmp_path, clock=lambda: t[0])
    for _ in range(4):
        m.feed(rng.normal(0, 300, 8000).astype(np.int16)); t[0] += 0.5
    assert not (tmp_path / "ambient").exists()
    (tmp_path / "tap").touch()
    for _ in range(8):
        m.feed(rng.normal(0, 300, 8000).astype(np.int16)); t[0] += 0.5
    (tmp_path / "tap").unlink()
    m.feed(rng.normal(0, 300, 8000).astype(np.int16))   # switched off: what was kept is written
    saved = np.load(next((tmp_path / "ambient").glob("*.npz")))
    assert saved["windows"].shape[1:] == (WINDOW, 96) and len(saved["times"]) == len(saved["windows"])


def test_the_tap_stops_by_itself(tmp_path):
    t = [1000.0]
    m = WakeModel(MODELS, tmp_path, clock=lambda: t[0])
    (tmp_path / "tap").touch()
    for _ in range(4):                                   # enough audio for a whole window
        m.feed(rng.normal(0, 300, 8000).astype(np.int16)); t[0] += 0.5
    t[0] += WakeModel.TAP_FOR_S + 1
    m.feed(rng.normal(0, 300, 8000).astype(np.int16))
    assert not (tmp_path / "tap").exists()
