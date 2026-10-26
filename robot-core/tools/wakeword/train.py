"""
Train the "spooky" detector on spooky's own feature code, and say how good it is.

    python <robot-core>/tools/wakeword/train.py   → models/spooky.npz and a report

Run from a work folder holding voices/ (Piper voices), data/ (gen_clips.py's clips,
openWakeWord's public features, the robot's room windows) and models/ (the two
feature models); copy models/spooky.npz and report.json to robot-core/models/wakeword/.

Positives: the name by 119 synthetic voices. Negatives: sound-alike words, everyday
talk, room-like noise, and — when present — real room features from the robot's tap
(data/ambient/*.npz). Validation holds out whole voices (serena, VCTK speakers 94+)
and a fifth of the talk: a new voice is what the owner's will be.
"""

import glob
import json
import sys
from pathlib import Path

import numpy as np
from sklearn.neural_network import MLPClassifier

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))        # robot-core
from services.wakeword import WINDOW, WakeFeatures  # noqa: E402

rng = np.random.default_rng(7)
F = WakeFeatures(Path("models"))
RATE = 16000


def noise(n):
    white = rng.normal(0, 1, n)
    pink = np.cumsum(white) * 0.02 + white * 0.5
    pink -= np.convolve(pink, np.ones(400) / 400, mode="same")
    return pink / (pink.std() + 1e-9) * 32768 * 10 ** (rng.uniform(-55, -35) / 20)


def stream(clip):
    """Every window of a clip, fed as the robot feeds it, after two seconds of room."""
    F.reset()
    lead = np.clip(noise(2 * RATE), -32768, 32767).astype(np.int16)
    F.feed(lead)
    out = []
    for i in range(0, len(clip), 4000):                     # the robot's ~0.25 s pieces, more or less
        n = F.feed(clip[i:i + 4000])
        frames = F.frames
        for back in range(n - 1, -1, -1):
            end = len(frames) - back
            if end >= WINDOW:
                out.append(frames[end - WINDOW:end].copy())
    return out


from openwakeword.utils import AudioFeatures  # noqa: E402
OWW = AudioFeatures(inference_framework="onnx")


def batch(clip):
    """The same clip through openWakeWord's batch features — how the public negatives were made.
    Both forms of every clip go in, so the form itself tells the model nothing."""
    lead = np.clip(noise(2 * RATE), -32768, 32767).astype(np.int16)
    x = np.concatenate([lead, clip])
    x = x[len(x) % 1280:]                                    # whole 80 ms chunks, keeping the end
    frames = OWW.embed_clips(x[None, :], batch_size=1)[0]
    return [frames[e - WINDOW:e].copy() for e in range(WINDOW, len(frames) + 1)]


def both(clip, pick):
    return pick(stream(clip)) + pick(batch(clip))


def held_out(meta):
    voice, spk = meta.split(":")[:2]
    return voice == "it_IT-serena-medium" or (voice == "en_GB-vctk-medium" and int(spk) >= 94)


def load(name):
    d = np.load(f"data/{name}.npz", allow_pickle=True)
    return d["clips"], d["meta"]


def main():
    X, y, val = [], [], []                                   # val: True → validation
    clips, meta = load("positives")
    for c, m in zip(clips, meta):
        w = both(c, lambda ws: ws[-3:])                      # ending at the name's end, and just before
        X += w; y += [1] * len(w); val += [held_out(m)] * len(w)
    print("positives", sum(y), flush=True)
    clips, meta = load("near")
    for c, m in zip(clips, meta):
        w = both(c, lambda ws: ws[-6:])
        X += w; y += [0] * len(w); val += [held_out(m)] * len(w)
    clips, meta = load("speech")
    talk_val = rng.random(len(clips)) < 0.2
    for c, v in zip(clips, talk_val):
        w = both(c, lambda ws: ws[::2])
        X += w; y += [0] * len(w); val += [bool(v)] * len(w)
    for _ in range(40):                                      # room noise alone
        w = stream(np.clip(noise(8 * RATE), -32768, 32767).astype(np.int16))[::4]
        X += w; y += [0] * len(w); val += [False] * len(w)
    # Public negatives: openWakeWord's validation set, 10.7 h of real audio as features. The
    # last fifth, in one block, is kept out to measure false alarms per hour honestly.
    public = np.load("data/validation_set_features.npy", mmap_mode="r")
    cut = int(len(public) * 0.8)
    for e in range(WINDOW, cut, 4):
        X.append(np.asarray(public[e - WINDOW:e])); y.append(0); val.append(False)
    acav = Path("data/acav_2000h.npy")
    if acav.exists():                                        # 2000 h, when downloaded: a sample
        big = np.load(acav, mmap_mode="r")
        for i in rng.choice(len(big), min(len(big), 300_000), replace=False):
            X.append(np.asarray(big[i], dtype=np.float32)); y.append(0); val.append(False)
        print("ACAV100M windows added", flush=True)
    ambient = sorted(glob.glob("data/ambient/*.npz"))
    for i, f in enumerate(ambient):                          # the real room, from the robot's tap
        w = list(np.load(f)["windows"].astype(np.float32))
        X += w; y += [0] * len(w); val += [i % 5 == 0] * len(w)
    print("negatives", len(y) - sum(y), f"({len(ambient)} real room files)", flush=True)

    X = np.stack(X).reshape(len(X), -1).astype(np.float32)
    y, val = np.array(y), np.array(val)
    mean, std = X[~val].mean(0), X[~val].std(0) + 1e-6
    Xn = (X - mean) / std
    tr = ~val
    pos = np.where(tr & (y == 1))[0]
    reps = max(1, int((tr & (y == 0)).sum() / (3 * len(pos))))   # positives at about a quarter
    idx = np.concatenate([np.where(tr)[0]] + [pos] * (reps - 1))
    clf = MLPClassifier(hidden_layer_sizes=(64,), alpha=1e-3, max_iter=60, early_stopping=True,
                        validation_fraction=0.1, random_state=0)
    clf.fit(Xn[idx], y[idx])

    # False alarms per hour: every frame of the held-out public block, as the robot scores them,
    # with alarms closer than a second counted once.
    hold = np.asarray(public[cut:], dtype=np.float32)
    hw = np.stack([hold[e - WINDOW:e] for e in range(WINDOW, len(hold) + 1)]).reshape(-1, WINDOW * 96)
    ph = clf.predict_proba((hw - mean) / std)[:, 1]
    held_hours = len(hold) * 0.08 / 3600

    def alarms(th):
        hits, last = 0, -99
        for i in np.where(ph >= th)[0]:
            if i - last > 12:
                hits += 1
            last = i
        return hits

    p = clf.predict_proba(Xn[val])[:, 1]
    yv = y[val]
    talk_windows = (val & (y == 0)).sum()
    hours = talk_windows * 2 * 0.08 / 3600                   # talk windows were taken every 2 frames
    report = {}
    print(f"held-out real audio: {held_hours:.1f} h")
    for th in (0.5, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99):
        recall = float((p[yv == 1] >= th).mean())
        talk = int((p[yv == 0] >= th).sum())
        per_hour = alarms(th) / held_hours
        report[th] = (round(recall, 3), talk, round(per_hour, 2))
        print(f"threshold {th:.2f}: unseen voices' names found {recall:.1%} · false alarms "
              f"{per_hour:.2f}/h on real audio · {talk} synthetic talk/sound-alike windows above it")
    best = min((th for th in report if report[th][2] <= 0.5), default=0.99)
    np.savez("models/spooky.npz", w1=clf.coefs_[0], b1=clf.intercepts_[0], w2=clf.coefs_[1][:, 0],
             b2=clf.intercepts_[1][0], mean=mean, std=std, threshold=best)
    json.dump({str(k): v for k, v in report.items()} | {"threshold": best}, open("models/report.json", "w"))
    print("saved models/spooky.npz, threshold", best)


if __name__ == "__main__":
    main()
