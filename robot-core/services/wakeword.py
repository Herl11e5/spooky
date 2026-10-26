"""
services/wakeword.py — A model that listens for "spooky" and nothing else.

The Vosk grammar spotter has to choose between the name and [unk], so in other
people's talk it sometimes chooses the name (services/audio.py, WakeSpotter). A
dedicated detector, trained only on the name, is the lasting answer. This is its
robot side, built on openWakeWord's design (github.com/dscripka/openWakeWord,
Apache-2.0): audio → mel spectrogram → a general speech embedding, one 96-number
frame every 80 ms → a small classifier over the last 16 frames (1.28 s).

    WakeFeatures  the streaming features, a port of openWakeWord's AudioFeatures
                  (onnxruntime only, already on the robot for Piper). The training
                  scripts on the Mac use this same class, so the classifier sees on
                  the robot exactly the numbers it was trained on.
    WakeModel     the classifier (one hidden layer, weights in an .npz, numpy only),
                  and two switches, both off unless a file says so:
                  - shadow: score every frame and log what it would have done,
                    deciding nothing — to measure it against the spotter first;
                  - tap: keep windows of features (never audio) of the room, one a
                    second with its time, as real negatives for training. It stops
                    by itself after eight hours.

Files: models/wakeword/ (in the repo) melspectrogram.onnx, embedding_model.onnx — the
shared feature models — and spooky.npz, the classifier (tools/wakeword/ trains it);
data/wakeword/ (survives releases) tap — present → collecting — and ambient/*.npz.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

log = logging.getLogger(__name__)

CHUNK = 1280          # 80 ms at 16 kHz: one embedding frame
WINDOW = 16           # frames the classifier sees (1.28 s)
_MEL_WINDOW, _MEL_STEP = 76, 8


class WakeFeatures:
    """16 kHz int16 audio in, one 96-number embedding per 80 ms out (openWakeWord's streaming)."""

    def __init__(self, models: Path, threads: int = 1) -> None:
        import onnxruntime as ort
        opts = ort.SessionOptions()
        opts.inter_op_num_threads = opts.intra_op_num_threads = threads
        self._mel = ort.InferenceSession(str(Path(models) / "melspectrogram.onnx"), sess_options=opts,
                                         providers=["CPUExecutionProvider"])
        self._emb = ort.InferenceSession(str(Path(models) / "embedding_model.onnx"), sess_options=opts,
                                         providers=["CPUExecutionProvider"])
        self.reset()

    def reset(self) -> None:
        self._raw = np.zeros(0, dtype=np.int16)        # the last samples, for the overlap
        self._rest = np.zeros(0, dtype=np.int16)       # samples short of a whole chunk
        self._mel_buf = np.ones((_MEL_WINDOW, 32), dtype=np.float32)
        self.frames = np.zeros((0, 96), dtype=np.float32)

    def _melspec(self, x: np.ndarray) -> np.ndarray:
        spec = np.squeeze(self._mel.run(None, {"input": x.astype(np.float32)[None, :]})[0])
        return spec / 10 + 2                            # openWakeWord's transform

    def _embed(self, mel: np.ndarray) -> np.ndarray:
        return self._emb.run(None, {"input_1": mel.astype(np.float32)[None, :, :, None]})[0].squeeze()

    def feed(self, samples: np.ndarray) -> int:
        """Add audio; return how many new embedding frames it produced (appended to .frames)."""
        x = np.concatenate([self._rest, np.asarray(samples, dtype=np.int16)])
        whole = len(x) // CHUNK * CHUNK
        self._rest = x[whole:]
        new = 0
        for start in range(0, whole, CHUNK):
            chunk = x[start:start + CHUNK]
            self._raw = np.concatenate([self._raw, chunk])[-(CHUNK + 160 * 3):]   # 3 frames of overlap
            self._mel_buf = np.vstack([self._mel_buf, self._melspec(self._raw)])[-10 * 97:]
            window = self._mel_buf[-_MEL_WINDOW:]
            if window.shape[0] == _MEL_WINDOW:
                self.frames = np.vstack([self.frames, self._embed(window)[None, :]])[-120:]
                new += 1
        return new

    def window(self) -> Optional[np.ndarray]:
        """The last WINDOW frames, or None before there are that many."""
        return self.frames[-WINDOW:].copy() if len(self.frames) >= WINDOW else None


class Classifier:
    """One hidden layer, ReLU, sigmoid out; weights from scikit-learn's MLPClassifier."""

    def __init__(self, path: Path) -> None:
        w = np.load(path)
        self.w1, self.b1, self.w2, self.b2 = w["w1"], w["b1"], w["w2"], w["b2"]
        self.mean, self.std = w["mean"], w["std"]
        self.threshold = float(w["threshold"]) if "threshold" in w else 0.5

    def score(self, window: np.ndarray) -> float:
        x = (window.reshape(-1) - self.mean) / self.std
        h = np.maximum(0.0, x @ self.w1 + self.b1)
        z = float(h @ self.w2 + self.b2)
        return 1.0 / (1.0 + np.exp(-z))


class WakeModel:
    """Features for every chunk of the microphone; shadow scores and the tap when switched on."""

    TAP_EVERY_S = 1.0
    TAP_FOR_S = 8 * 3600

    def __init__(self, models: Path, data: Path, clock: Callable[[], float] = time.time) -> None:
        self._dir = Path(data)
        self._clock = clock
        self._features = WakeFeatures(Path(models))
        model = Path(models) / "spooky.npz"
        self._clf = Classifier(model) if model.exists() else None
        self._tap: list = []
        self._tap_times: list = []
        self._tap_scores: list = []
        self._tap_started = None
        self._tap_last = 0.0
        self._above_at = -1e9
        self.last_score = 0.0
        self.best_back = 0
        self.shadow = True            # False once it decides (audio.wake_detector: model)
        log.info(f"WakeModel: features on{', classifier in shadow mode' if self._clf else ''}")

    def feed(self, samples: np.ndarray) -> Optional[float]:
        """The highest score in this audio (None without a classifier)."""
        new = self._features.feed(samples)
        if not new:
            return None
        window = self._features.window()
        if window is None:
            return None
        if self._clf is None:
            self._collect(window, None)
            return None
        best = 0.0
        self.best_back = 0                  # how many frames before the end the best one was
        for back in range(new):             # every new frame, not only the last
            frames = self._features.frames[len(self._features.frames) - back - WINDOW:
                                           len(self._features.frames) - back]
            if len(frames) == WINDOW:
                s = self._clf.score(frames)
                if s > best:
                    best, self.best_back = s, back
        self.last_score = best
        self._collect(window, best)
        now = self._clock()
        if best >= self._clf.threshold:
            if now - self._above_at > 1.0 and self.shadow:   # one event, not one line per frame
                log.info(f"WakeModel (shadow): would wake — score {best:.2f}")
            self._above_at = now
        return best

    @property
    def threshold(self) -> Optional[float]:
        return self._clf.threshold if self._clf else None

    def _collect(self, window: np.ndarray, score: Optional[float]) -> None:
        """One window a second, and every window the model is drawn to (score >= 0.5): the
        room's near misses are the most useful negatives. Training leaves out those near a
        real call, which the logs date."""
        flag = self._dir / "tap"
        now = self._clock()
        if not flag.exists():
            if self._tap:
                self.flush()
            self._tap_started = None
            return
        if self._tap_started is None:
            self._tap_started = now
            log.info("WakeModel: tap on — keeping room features (never audio) for training")
        if now - self._tap_started > self.TAP_FOR_S:
            flag.unlink(missing_ok=True)
            log.info("WakeModel: tap off after eight hours")
            return
        drawn = score is not None and score >= 0.5
        if drawn or now - self._tap_last >= self.TAP_EVERY_S:
            if not drawn:
                self._tap_last = now
            self._tap.append(window.astype(np.float16))
            self._tap_times.append(now)
            self._tap_scores.append(-1.0 if score is None else score)
            if len(self._tap) >= 900:          # a file every quarter of an hour
                self.flush()

    def flush(self) -> None:
        if not self._tap:
            return
        out = self._dir / "ambient" / time.strftime("ambient-%Y%m%d-%H%M%S.npz", time.localtime(self._tap_times[0]))
        out.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(out, windows=np.stack(self._tap), times=np.array(self._tap_times),
                            scores=np.array(self._tap_scores))
        log.info(f"WakeModel: {len(self._tap)} room windows saved to {out.name}")
        self._tap, self._tap_times, self._tap_scores = [], [], []
