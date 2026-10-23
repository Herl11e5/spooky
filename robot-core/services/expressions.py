"""
services/expressions.py — Expressions read against each person's own resting face.

The body's expression model (MobileFaceNet, RAF-DB) reads every face against faces
in general, and some resting faces sit close to an expression: the owner's sad face
(2026-10-22) came out as neutral — sad rose, never past neutral. So each known
person gets a baseline, the slow average of the seven probabilities while the model
reads them as neutral, and an expression counts when it stands clearly above that
person's own usual, even when it is not the model's first choice.

The baseline learns only from neutral moments and slowly (a ten-minute time
constant): a minute of laughing must not make laughing the usual, nor a sad
quarter of an hour hide inside its own average. Each moment is judged before it
is learnt from. Until a person has been watched for a few minutes, nothing is
inferred. Baselines are kept next to the person's face data.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

log = logging.getLogger(__name__)

EXPRESSIONS = ("angry", "disgust", "fearful", "happy", "neutral", "sad", "surprised")
_NEUTRAL = EXPRESSIONS.index("neutral")


class ExpressionBaseline:

    def __init__(self, root: Path, tau_s: float = 600.0, warm_s: float = 180.0,
                 min_lift: float = 0.15, min_ratio: float = 1.8,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._root = Path(root)
        self._tau, self._warm = tau_s, warm_s
        self._min_lift, self._min_ratio = min_lift, min_ratio
        self._clock = clock
        self._people: dict = {}          # person_id → {"mean": array, "seen_s": float, "at": float}

    def _get(self, pid: str) -> dict:
        if pid not in self._people:
            entry = {"mean": None, "seen_s": 0.0, "at": None, "saved": 0.0}
            try:
                saved = json.loads((self._root / pid / "expression_baseline.json").read_text())
                entry.update(mean=np.array(saved["mean"], dtype=float), seen_s=float(saved["seen_s"]))
            except (OSError, ValueError, KeyError):
                pass
            self._people[pid] = entry
        return self._people[pid]

    def _save(self, pid: str, entry: dict) -> None:
        path = self._root / pid / "expression_baseline.json"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"mean": [round(float(x), 4) for x in entry["mean"]],
                                       "seen_s": round(entry["seen_s"], 1)}))
            tmp.replace(path)
        except OSError as e:
            log.debug(f"ExpressionBaseline: not saved for {pid}: {e}")

    def forget(self, pid: str) -> None:
        """The file goes with the person's face folder; this forgets the copy in memory."""
        self._people.pop(pid, None)

    def usual(self, pid: str) -> Optional[dict]:
        entry = self._get(pid)
        if entry["mean"] is None:
            return None
        return {e: round(float(v), 3) for e, v in zip(EXPRESSIONS, entry["mean"])}

    def judge(self, pid: str, probs: dict) -> Optional[tuple]:
        """
        (expression, probability, the person's usual) when an expression stands out
        from this person's resting face; then learn from the moment if it was neutral.
        """
        v = np.array([float(probs.get(e) or 0.0) for e in EXPRESSIONS])
        now = self._clock()
        entry = self._get(pid)
        found = None
        mean = entry["mean"]
        if mean is not None and entry["seen_s"] >= self._warm:
            lift = v - mean
            ratio = v / np.maximum(mean, 0.02)
            best = None
            for i, name in enumerate(EXPRESSIONS):
                if i != _NEUTRAL and lift[i] >= self._min_lift and ratio[i] >= self._min_ratio:
                    if best is None or lift[i] > lift[best]:
                        best = i
            if best is not None:
                found = (EXPRESSIONS[best], round(float(v[best]), 2), round(float(mean[best]), 2))
        # Learn only from moments the model reads as neutral: the resting face.
        dt = 0.0 if entry["at"] is None else min(1.0, max(0.0, now - entry["at"]))
        entry["at"] = now
        if int(np.argmax(v)) == _NEUTRAL and found is None:
            if mean is None:
                entry["mean"] = v
            else:
                entry["mean"] = mean + (dt / self._tau) * (v - mean)
            entry["seen_s"] += dt
            if entry["seen_s"] - entry["saved"] >= 30.0:
                entry["saved"] = entry["seen_s"]
                self._save(pid, entry)
        return found
