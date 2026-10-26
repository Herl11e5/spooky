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


class Picked:
    """Rows of a memory-mapped file, read only when the training array is filled."""

    def __init__(self, source, rows):
        self.source, self.rows = source, rows

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, mask):
        return Picked(self.source, self.rows[mask])

    def into(self, out):
        for i in range(0, len(self.rows), 50_000):
            part = self.source[self.rows[i:i + 50_000]]
            out[i:i + len(part)] = part.reshape(len(part), -1)


class Rows:
    """Windows gathered for training, early stopping and validation, as blocks: the
    1536-wide rows (several GB of them) are copied once, into one array per use."""

    STOP_SHARE = 0.1                                         # watched for early stopping

    def __init__(self):
        self.blocks = {"train": [], "stop": [], "val": []}   # (windows, label)

    def add(self, windows, label, held=False, times=1):
        if isinstance(windows, list):
            if not windows:
                return
            windows = np.stack(windows)
        if held:
            self.blocks["val"].append((windows, label))
            return
        stop = rng.random(len(windows)) < self.STOP_SHARE
        self.blocks["stop"].append((windows[stop], label))
        self.blocks["train"] += [(windows[~stop], label)] * times

    def count(self, use, label):
        return sum(len(b) for b, l in self.blocks[use] if l == label)

    def array(self, use, name_times=1):
        blocks = [(b, l) for b, l in self.blocks.pop(use) for _ in range(name_times if l == 1 else 1)]
        n = sum(len(b) for b, _ in blocks)
        X, y = np.empty((n, WINDOW * 96), np.float32), np.empty(n, np.int64)
        at = 0
        while blocks:                                        # each block freed once copied
            b, label = blocks.pop(0)
            k = len(b)
            if isinstance(b, Picked):
                b.into(X[at:at + k])
            else:
                X[at:at + k] = np.asarray(b, dtype=np.float32).reshape(k, WINDOW * 96)
            y[at:at + k] = label
            at += k
        return X, y


def column_stats(X, chunk=100_000):
    """Mean and spread of every feature, a chunk at a time (no copy of X)."""
    s, q = np.zeros(X.shape[1]), np.zeros(X.shape[1])
    for i in range(0, len(X), chunk):
        c = X[i:i + chunk].astype(np.float64)
        s += c.sum(0)
        q += (c * c).sum(0)
    mean = s / len(X)
    std = np.sqrt(np.maximum(q / len(X) - mean ** 2, 0)) + 1e-6
    return mean.astype(np.float32), std.astype(np.float32)


def normalise(X, mean, std, chunk=100_000):
    for i in range(0, len(X), chunk):
        X[i:i + chunk] -= mean
        X[i:i + chunk] /= std


def fit(X, y, X_stop, y_stop, epochs=60, patience=10):
    """sklearn's MLP, one pass at a time, kept at its best on the early-stopping windows:
    its own early stopping would copy the whole training set once more."""
    clf = MLPClassifier(hidden_layer_sizes=(64,), alpha=1e-3, random_state=0)
    best, since, kept = -1.0, 0, None
    for epoch in range(epochs):
        clf.partial_fit(X, y, classes=[0, 1])
        p = clf.predict(X_stop)
        score = float(np.mean([(p[y_stop == c] == c).mean() for c in (0, 1)]))   # balanced accuracy
        print(f"  pass {epoch + 1}: early-stopping balanced accuracy {score:.4f}", flush=True)
        if score > best + 1e-4:
            best, since = score, 0
            kept = ([a.copy() for a in clf.coefs_], [b.copy() for b in clf.intercepts_])
        else:
            since += 1
            if since >= patience:
                break
    clf.coefs_, clf.intercepts_ = kept
    return clf


def main():
    rows = Rows()
    clips, meta = load("positives")
    for c, m in zip(clips, meta):
        rows.add(both(c, lambda ws: ws[-3:]), 1, held_out(m))    # ending at the name's end, and just before
    print("positives", len(clips), "clips", flush=True)
    clips, meta = load("near")
    for c, m in zip(clips, meta):
        rows.add(both(c, lambda ws: ws[-6:]), 0, held_out(m))
    clips, meta = load("speech")
    talk_val = rng.random(len(clips)) < 0.2
    for c, v in zip(clips, talk_val):
        rows.add(both(c, lambda ws: ws[::2]), 0, bool(v))
    for _ in range(40):                                      # room noise alone
        rows.add(stream(np.clip(noise(8 * RATE), -32768, 32767).astype(np.int16))[::4], 0)
    # Public negatives: openWakeWord's validation set, 10.7 h of real audio as features. The
    # last fifth, in one block, is kept out to measure false alarms per hour honestly.
    public = np.load("data/validation_set_features.npy", mmap_mode="r")
    cut = int(len(public) * 0.8)
    rows.add(np.lib.stride_tricks.sliding_window_view(public[:cut], (WINDOW, 96))[::4, 0], 0)
    acav = next((p for p in ("data/acav_2000h.npy", "data/acav_2000h.npy.partial") if Path(p).exists()), None)
    if acav:                                                 # 2000 h (or what has arrived): a sample
        big = partial_npy(acav)
        pick = np.sort(rng.choice(len(big), min(len(big), int(os.environ.get("ACAV_SAMPLE", 300_000))),
                                  replace=False))
        rows.add(Picked(big, pick), 0)
        print(f"ACAV100M: {len(pick)} windows of {len(big)} arrived", flush=True)
    # The real room, from the robot's tap: windows near a wake left out (maybe the owner),
    # the near misses (score >= 0.5) counted five times. The most recent quarter of the
    # room is held out: trained on the past, judged on what came after, as in use.
    ambient, wakes = sorted(glob.glob("data/ambient/ambient-*.npz")), wake_times()
    sizes = [len(np.load(f)["times"]) for f in ambient]
    held_from = len(ambient)
    while held_from > 0 and sum(sizes[held_from - 1:]) <= 0.25 * max(sum(sizes), 1):
        held_from -= 1
    room_val, room_times = [], []
    for i, f in enumerate(ambient):
        d = np.load(f)
        keep = np.array([not (len(wakes) and np.abs(wakes - t).min() < 15) for t in d["times"]], dtype=bool)
        windows = d["windows"][keep].astype(np.float32)
        scores = (d["scores"] if "scores" in d else np.full(len(keep), -1.0))[keep]
        if i >= held_from:
            room_val.append(windows); room_times.append(d["times"][keep])
            continue
        rows.add(windows[scores < 0.5], 0)
        rows.add(windows[scores >= 0.5], 0, times=5)
    print(f"{len(ambient)} room files, the last {len(ambient) - held_from} held out", flush=True)

    reps = max(1, int(rows.count("train", 0) / (3 * rows.count("train", 1))))   # positives at about a quarter
    X, y = rows.array("train", name_times=reps)
    X_stop, y_stop = rows.array("stop")
    X_val, y_val = rows.array("val")
    print(f"training on {len(X)} windows ({int(y.sum())} of the name), {X.nbytes / 1e9:.1f} GB", flush=True)
    mean, std = column_stats(X)
    for a in (X, X_stop, X_val):
        normalise(a, mean, std)
    clf = fit(X, y, X_stop, y_stop)
    del X, X_stop

    # False alarms per hour: every frame of the held-out public block, as the robot scores them,
    # with alarms closer than a second counted once.
    hold = np.asarray(public[cut:], dtype=np.float32)
    hw = np.lib.stride_tricks.sliding_window_view(hold, (WINDOW, 96))[:, 0].reshape(-1, WINDOW * 96)
    ph = clf.predict_proba((hw - mean) / std)[:, 1]
    held_hours = len(hold) * 0.08 / 3600

    def alarms(th):
        hits, last = 0, -99
        for i in np.where(ph >= th)[0]:
            if i - last > 12:
                hits += 1
            last = i
        return hits

    p = clf.predict_proba(X_val)[:, 1]
    report = {}
    print(f"held-out real audio: {held_hours:.1f} h")
    for th in (0.5, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99):
        recall = float((p[y_val == 1] >= th).mean())
        talk = int((p[y_val == 0] >= th).sum())
        per_hour = alarms(th) / held_hours
        report[th] = (round(recall, 3), talk, round(per_hour, 2))
        print(f"threshold {th:.2f}: unseen voices' names found {recall:.1%} · false alarms "
              f"{per_hour:.2f}/h on real audio · {talk} synthetic talk/sound-alike windows above it")
    best = min((th for th in report if report[th][2] <= 0.5), default=0.99)
    if room_val:
        rt = np.concatenate(room_times)
        pr = clf.predict_proba((np.concatenate(room_val).reshape(len(rt), -1) - mean) / std)[:, 1]
        room_hours = len(np.unique(rt.astype(int))) / 3600
        for th in sorted({0.7, 0.8, 0.9, best}):
            hits = np.sort(rt[pr >= th])
            events = int(len(hits) and 1 + (np.diff(hits) > 1.0).sum())
            print(f"held-out room ({room_hours:.1f} h) at {th}: {events} alarms ({events / room_hours:.1f}/h)")
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
