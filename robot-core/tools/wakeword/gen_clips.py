"""
Synthetic clips for the "spooky" wake word: the name said by 119 voices, and words
that sound like it (the ones that woke the robot by mistake), all at 16 kHz.

Run from the work folder (see train.py).

    python gen_clips.py positives   → data/positives.npz   (the name, padded with noise)
    python gen_clips.py near        → data/near.npz        (sound-alike words: hard negatives)
"""

import io
import json
import sys
import wave
from pathlib import Path

import numpy as np
from piper import SynthesisConfig
from piper.voice import PiperVoice

RATE = 16000
rng = np.random.default_rng(23)
VOICES = sorted(Path("voices").glob("*.onnx"))

EN_NAME = ["Spooky", "Spooky!", "Spooky?", "Hey Spooky", "Spooky,", "Oh Spooky"]
IT_NAME = ["Spuchi", "Spuki", "Spooky", "Ehi Spuchi", "Spùchi!", "Ciao Spuchi", "Spuchi?"]
EN_AFTER = ["come here", "how are you", "look at me", "stop"]
IT_AFTER = ["come stai", "vieni qua", "che ore sono", "siediti", "guardami"]
# Words that woke it by mistake (2026-10-23), and other Italian words that start the same way.
NEAR = ["sposato", "spunti", "sputi", "Spoleto", "sporco", "spuntino", "spogli", "scusi", "spot",
        "spiccioli", "ospiti", "spuma", "spugna", "Spagna", "spingi", "subito", "spazio", "spesso",
        "sbuffi", "sgocciola", "scuola", "spalla", "sportello", "spedire", "spettacolo"]
# Not here: "sbuchi", "sporchi", "spurghi", "sfoghi", "sfoci", "buchi", "pochi" — that is how the
# recogniser wrote the owner's own "Spooky": as negatives they would teach it to ignore him.


def speakers(voice: Path) -> int:
    return json.loads(voice.with_suffix(".onnx.json").read_text()).get("num_speakers", 1) or 1


def say(model: PiperVoice, text: str, speaker: int) -> np.ndarray:
    cfg = SynthesisConfig(speaker_id=speaker if speaker else None,
                          length_scale=float(rng.uniform(0.8, 1.35)),
                          noise_scale=float(rng.uniform(0.4, 0.9)),
                          noise_w_scale=float(rng.uniform(0.5, 1.0)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        model.synthesize_wav(text, w, syn_config=cfg)
    buf.seek(0)
    with wave.open(buf) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
    if rate != RATE:
        x = np.interp(np.linspace(0, len(x) - 1, int(len(x) * RATE / rate)), np.arange(len(x)), x)
    return x


def noise(n: int) -> np.ndarray:
    """Room-like noise: pink-ish, at a random level (the robot's room sits near -42 dBFS)."""
    white = rng.normal(0, 1, n)
    pink = np.cumsum(white) * 0.02 + white * 0.5
    pink -= np.convolve(pink, np.ones(400) / 400, mode="same")
    level = 32768 * 10 ** (rng.uniform(-55, -35) / 20)
    return pink / (pink.std() + 1e-9) * level


def clip(speech: np.ndarray, before: float, after: float) -> np.ndarray:
    pre, post = int(before * RATE), int(after * RATE)
    x = np.concatenate([np.zeros(pre), speech, np.zeros(post)])
    x = x / (np.abs(x).max() + 1e-9) * 32768 * 10 ** (rng.uniform(-14, -3) / 20)   # near or far
    return np.clip(x + noise(len(x)), -32768, 32767).astype(np.int16)


def positives():
    clips, meta = [], []
    for v in VOICES:
        model = PiperVoice.load(str(v))
        italian = v.name.startswith("it_")
        n_spk = speakers(v)
        per = 6 if n_spk > 50 else 15 if n_spk > 1 else (40 if italian else 25)
        for spk in range(n_spk):
            for _ in range(per):
                text = str(rng.choice(IT_NAME if italian else EN_NAME))
                speech = say(model, text, spk)
                if rng.random() < 0.4:                       # a command follows straight away
                    tail = say(model, str(rng.choice(IT_AFTER if italian else EN_AFTER)), spk)
                    speech = np.concatenate([speech, tail[: int(RATE * rng.uniform(0.05, 0.3))]])
                clips.append(clip(speech, rng.uniform(0.6, 1.4), rng.uniform(0.0, 0.25)))
                meta.append(f"{v.stem}:{spk}:{text}")
        print(v.stem, len(clips), flush=True)
    save("data/positives.npz", clips, meta)


def near():
    clips, meta = [], []
    for v in VOICES:
        model = PiperVoice.load(str(v))
        n_spk = speakers(v)
        for spk in (range(n_spk) if n_spk < 50 else rng.choice(n_spk, 30, replace=False)):
            for word in rng.choice(NEAR, 8 if n_spk < 50 else 3, replace=False):
                clips.append(clip(say(model, str(word), int(spk)), rng.uniform(0.6, 1.4), rng.uniform(0.0, 0.25)))
                meta.append(f"{v.stem}:{spk}:{word}")
        print(v.stem, len(clips), flush=True)
    save("data/near.npz", clips, meta)


def speech():
    """Everyday talk, Italian and English, in streams of a few sentences: what must not wake it."""
    import json as _json
    sentences = _json.loads(Path("data/sentences.json").read_text())
    models = {v.stem: (PiperVoice.load(str(v)), speakers(v)) for v in VOICES}
    it_voices = [k for k in models if k.startswith("it_")]
    en_voices = [k for k in models if k.startswith("en_")]
    streams, meta, current, cur_meta = [], [], [], []
    for lang, voices in (("it", it_voices), ("en", en_voices)):
        for sentence in sentences[lang]:
            for _ in range(2 if lang == "it" else 1):
                name = str(rng.choice(voices))
                model, n_spk = models[name]
                spk = int(rng.integers(n_spk))
                current.append(say(model, sentence, spk))
                current.append(np.zeros(int(RATE * rng.uniform(0.05, 0.6))))     # a pause, or none
                cur_meta.append(f"{name}:{spk}")
                if len(current) >= 10:
                    streams.append(clip(np.concatenate(current), 0.2, 0.2))
                    meta.append(" ".join(cur_meta))
                    current, cur_meta = [], []
        print(lang, len(streams), flush=True)
    save("data/speech.npz", streams, meta)


def save(path, clips, meta):
    Path("data").mkdir(exist_ok=True)
    np.savez_compressed(path, clips=np.array(clips, dtype=object), meta=np.array(meta))
    print(path, len(clips), "clips")


if __name__ == "__main__":
    {"positives": positives, "near": near, "speech": speech}[sys.argv[1]]()
