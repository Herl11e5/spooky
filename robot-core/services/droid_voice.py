"""
services/droid_voice.py — Spooky's own language: whistles, blips and clicks.

A droid language in the spirit of R2-D2 or BB-8, made from scratch (no sound from
any film). The text spooky would have said becomes a string of tones: every word
turns into one to three syllables, one per vowel group, and each syllable's pitch,
glide, length and timbre come from a hash of the word — so the same word always
sounds the same and it feels like a language, not noise. The sentence's mood
shapes it all: a question rises at the end, an exclamation is brighter, sadness is
low and slow, alarm is fast and warbling. Small clicks between words, like legs on
a desk, are what makes it a spider's voice.

Pure numpy: a sentence renders in milliseconds on the Pi.

    wav = render("You're back! I missed you!")        # int16 mono, RATE Hz
    mood("Watch out, too close!")                      # "alert"
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass

import numpy as np

RATE = 22050

_VOWELS = re.compile(r"[aeiouyàèéìòóù]+", re.IGNORECASE)
_WORDS = re.compile(r"[\wÀ-ÿ']+|[.!?]", re.UNICODE)


@dataclass(frozen=True)
class Mood:
    low: float          # Hz, pitch range
    high: float
    speed: float        # > 1 is faster
    glide: float        # how far a syllable bends (fraction of its pitch)
    fall: float         # overall drift across the sentence (< 0 falls)
    loud: float
    warble: float       # share of warbling syllables


MOODS = {
    "neutral":  Mood(650, 1400, 1.00, 0.25, -0.05, 0.70, 0.05),
    "happy":    Mood(900, 2000, 1.20, 0.40, +0.10, 0.80, 0.05),
    "question": Mood(700, 1500, 1.05, 0.30, +0.15, 0.70, 0.05),
    "sad":      Mood(380, 800, 0.70, 0.30, -0.30, 0.55, 0.00),
    "alert":    Mood(1100, 2400, 1.50, 0.50, +0.05, 0.90, 0.50),
    "sleepy":   Mood(330, 650, 0.60, 0.20, -0.35, 0.45, 0.00),
}

_CUES = {
    "alert": ("watch out", "careful", "danger", "alert", "warning", "too close", "attenzione", "pericolo"),
    "sad": ("sorry", "sad", "couldn't", "can't", "cannot", "failed", "wrong", "miss you", "mi dispiace", "triste"),
    "sleepy": ("good night", "sleep", "tired", "buonanotte", "dormire", "stanco"),
    "happy": ("great", "yay", "love", "you're back", "hello", "hi ", "welcome", "got it", "ciao", "evviva", "bentornat"),
}


def mood(text: str) -> str:
    """A rough mood from the words and punctuation."""
    low = f" {text.lower()} "
    for name, cues in _CUES.items():
        if any(c in low for c in cues):
            return name
    if text.rstrip().endswith("?"):
        return "question"
    if "!" in text:
        return "happy"
    return "neutral"


def _rng(word: str, i: int) -> np.random.Generator:
    return np.random.default_rng(zlib.crc32(f"{word.lower()}#{i}".encode()))


def _envelope(n: int, attack: float = 0.006, release: float = 0.02) -> np.ndarray:
    env = np.ones(n)
    a, r = min(n // 3, int(attack * RATE)), min(n // 3, int(release * RATE))
    if a:
        env[:a] = np.linspace(0, 1, a)
    if r:
        env[-r:] = np.linspace(1, 0, r) ** 2
    return env


def _tone(f0: float, f1: float, dur: float, kind: str) -> np.ndarray:
    n = max(1, int(dur * RATE))
    t = np.arange(n) / RATE
    freq = f0 * (f1 / f0) ** (t / dur)                       # exponential glide
    if kind == "warble":
        freq = freq * (1 + 0.12 * np.sin(2 * np.pi * 28 * t))
    elif kind == "whistle":
        freq = freq * (1 + 0.015 * np.sin(2 * np.pi * 6 * t))
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    wave = np.sin(phase) + 0.18 * np.sin(2 * phase)          # a little second harmonic: rounder
    if kind == "blip":
        wave *= np.exp(-t / (dur * 0.35))
    elif kind == "chirp":
        wave = np.sin(phase + 1.6 * np.sin(phase * 0.5))      # FM: the "electronic" one
    return wave * _envelope(n)


def _click(rng: np.random.Generator) -> np.ndarray:
    n = int(0.006 * RATE)
    noise = rng.standard_normal(n)
    noise = np.convolve(noise, [1, -1], mode="same")          # bright, no thud
    return 0.35 * noise * np.exp(-np.arange(n) / (n / 5))


def render(text: str, feeling: str | None = None) -> np.ndarray:
    """The text in spooky's language: int16 mono at RATE Hz."""
    m = MOODS[feeling or mood(text)]
    tokens = _WORDS.findall(text)
    words = [w for w in tokens if w not in ".!?"]
    out: list[np.ndarray] = [np.zeros(int(0.05 * RATE))]
    total = max(1, len(words))
    k = 0
    for idx, tok in enumerate(tokens):
        if tok in ".!?":
            out.append(np.zeros(int((0.22 if tok == "." else 0.16) / m.speed * RATE)))
            continue
        k += 1
        drift = 1 + m.fall * (k / total)
        syllables = max(1, min(3, len(_VOWELS.findall(tok))))
        last_word = idx + 1 >= len(tokens) or tokens[idx + 1] in ".!?"
        for i in range(syllables):
            r = _rng(tok, i)
            f0 = r.uniform(m.low, m.high) * drift
            bend = r.uniform(-m.glide, m.glide)
            if last_word and i == syllables - 1:
                end = tokens[idx + 1] if idx + 1 < len(tokens) else "."
                bend = {"?": 0.9, "!": 0.35, ".": -0.35}.get(end, bend)   # sentence intonation
            kind = "warble" if r.random() < m.warble else r.choice(["whistle", "blip", "chirp", "whistle"])
            dur = r.uniform(0.06, 0.15) / m.speed * (1.4 if last_word and i == syllables - 1 else 1.0)
            out.append(m.loud * _tone(f0, f0 * (1 + bend), dur, kind))
            out.append(np.zeros(int(r.uniform(0.012, 0.03) / m.speed * RATE)))
        r = _rng(tok, 99)
        if r.random() < 0.45:                                   # the spider's legs
            out.append(_click(r))
        out.append(np.zeros(int(r.uniform(0.03, 0.07) / m.speed * RATE)))
    out.append(np.zeros(int(0.08 * RATE)))
    audio = np.concatenate(out)
    peak = np.abs(audio).max() or 1.0
    return (audio / peak * 0.85 * 32767).astype(np.int16)
