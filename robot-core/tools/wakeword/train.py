"""
Train the "spooky" detector on spooky's own feature code, and say how good it is.

    python <robot-core>/tools/wakeword/train.py [out.npz]   → models/spooky.npz and a report

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
import os
import sys
import wave
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


def partial_npy(path):
    """An .npy file, even one still downloading: the whole records that have arrived."""
    with open(path, "rb") as f:
        version = np.lib.format.read_magic(f)
        reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
        shape, _, dtype = reader(f)
        offset = f.tell()
    per = int(np.prod(shape[1:])) * np.dtype(dtype).itemsize
    n = min(shape[0], (os.path.getsize(path) - offset) // per)
    return np.memmap(path, dtype=dtype, mode="r", offset=offset, shape=(n,) + tuple(shape[1:]))


def wake_times():
    """When the robot woke (data/ambient/wakes.txt, epoch seconds): its tap windows near them
    may be the owner calling, and must not be taught as 'not the name'."""
    try:
        return np.array([float(x) for x in open("data/ambient/wakes.txt").read().split()])
    except OSError:
        return np.array([])


def owner_calls(score_fn):
    """The owner's own recorded calls (private/, never trained on): best score per call."""
    try:
        w = wave.open("private/calls.wav")
        spec = json.load(open("private/calls.json"))
    except OSError:
        return None
    audio = np.frombuffer(w.readframes(w.getnframes()), np.int16)
    F.reset()
    times, wins = [], []
    for i in range(0, len(audio), 8000):
        n = F.feed(audio[i:i + 8000])
        for back in range(n - 1, -1, -1):
            end = len(F.frames) - back
            if end >= WINDOW:
                times.append((i + 8000) / RATE - back * 0.08)
                wins.append(F.frames[end - WINDOW:end].reshape(-1))
    times, scores = np.array(times), score_fn(np.stack(wins))

    def best(a, b):
        m = (times >= a) & (times <= b + 1.2)
        return float(scores[m].max()) if m.any() else 0.0
    return [best(a, b) for a, b in spec["calls"]], [best(a, b) for a, b in spec["others"]]


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
    acav = next((p for p in ("data/acav_2000h.npy", "data/acav_2000h.npy.partial") if Path(p).exists()), None)
    if acav:                                                 # 2000 h (or what has arrived): a sample
        big = partial_npy(acav)
        pick = np.sort(rng.choice(len(big), min(len(big), int(os.environ.get("ACAV_SAMPLE", 600_000))),
                                  replace=False))
        w = np.asarray(big[pick], dtype=np.float32)
        X += list(w); y += [0] * len(w); val += [False] * len(w)
        print(f"ACAV100M: {len(w)} windows of {len(big)} arrived", flush=True)
    # The real room, from the robot's tap: windows near a wake left out (maybe the owner),
    # the near misses (score >= 0.5) counted five times. The most recent quarter of the
    # room is held out: trained on the past, judged on what came after, as in use.
    ambient, wakes = sorted(glob.glob("data/ambient/ambient-*.npz")), wake_times()
    sizes = [len(np.load(f)["times"]) for f in ambient]
    held_from = len(ambient)
    while held_from > 0 and sum(sizes[held_from - 1:]) <= 0.25 * max(sum(sizes), 1):
        held_from -= 1
    room_val = []
    for i, f in enumerate(ambient):
        d = np.load(f)
        keep = np.array([not (len(wakes) and np.abs(wakes - t).min() < 15) for t in d["times"]])
        scores = d["scores"] if "scores" in d else np.full(len(keep), -1.0)
        for wdw, s in zip(d["windows"][keep].astype(np.float32), scores[keep]):
            held = i >= held_from
            for _ in range(5 if s >= 0.5 and not held else 1):
                X.append(wdw); y.append(0); val.append(held)
            if held:
                room_val.append(wdw)
    print("negatives", len(y) - sum(y), f"({len(ambient)} room files, {len(room_val)} room windows held out)",
          flush=True)

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
    if room_val:
        pr = clf.predict_proba((np.stack(room_val).reshape(len(room_val), -1) - mean) / std)[:, 1]
        print(f"held-out room windows above {best}: {int((pr >= best).sum())} of {len(pr)}")
    owner = owner_calls(lambda w: clf.predict_proba((w - mean) / std)[:, 1])
    if owner:
        calls, others = owner
        print(f"owner's calls found at {best}: {sum(c >= best for c in calls)} of {len(calls)} "
              f"(scores {' '.join(f'{c:.2f}' for c in calls)}); someone else talking: "
              f"{' '.join(f'{o:.2f}' for o in others)}")
    out = sys.argv[1] if len(sys.argv) > 1 else "models/spooky.npz"
    np.savez(out, w1=clf.coefs_[0], b1=clf.intercepts_[0], w2=clf.coefs_[1][:, 0],
             b2=clf.intercepts_[1][0], mean=mean, std=std, threshold=best)
    json.dump({str(k): v for k, v in report.items()} | {"threshold": best}, open(out.replace(".npz", ".json"), "w"))
    print(f"saved {out}, threshold", best)


if __name__ == "__main__":
    main()
