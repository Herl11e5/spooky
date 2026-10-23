"""KnowledgeService: what spooky perceives, recalls and learns — with a fake embedder and model."""
import json
import re
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.bus import EventType
from services.knowledge import KnowledgeService


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


class BagOfWords:
    """Sentences that share words come out similar; good enough to test recall."""

    def __init__(self):
        self.calls, self.offline = 0, False

    def embed(self, texts, query=False):
        if self.offline:
            return None
        self.calls += 1
        out = np.zeros((len(texts), 256), np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-zà-ù]{3,}", t.lower()):
                out[i, zlib.crc32(w.encode()) % 256] += 1
        return out / np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)


class Bus:
    def __init__(self):
        self.handlers = {}

    def subscribe(self, event, fn):
        self.handlers.setdefault(event, []).append(fn)

    def emit(self, event, **payload):
        class Ev(dict):
            def get(self, k, d=None):
                return dict.get(self, k, d)
        for fn in self.handlers.get(event, []):
            fn(Ev(payload))


@pytest.fixture
def mem(tmp_path):
    clock, bus, emb = Clock(), Bus(), BagOfWords()
    replies = []
    chat = lambda prompt: replies.pop(0) if replies else json.dumps({"facts": []})
    k = KnowledgeService(bus, tmp_path / "knowledge.db", embedder=emb, chat=chat, clock=clock)
    return k, bus, emb, clock, replies


def test_everything_heard_is_kept_but_not_noise(mem):
    k, bus, *_ = mem
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="il meeting con Luca è alle tre", final=True)
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="il meeting con", final=False)      # partial
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="eh sì", final=True)                # too short
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="il meeting con Luca è alle tre", final=True)  # repeat
    assert k.stats()["pending"] == 1


def test_ambient_listening_can_be_switched_off(mem, tmp_path):
    k, bus, emb, clock, _ = mem
    k.set_ambient(False)
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="questa non deve restare", final=True)
    bus.emit(EventType.COMMAND_PARSED, command="spooky ricordati del dentista")      # spoken to it: kept
    assert k.stats()["pending"] == 1
    again = KnowledgeService(Bus(), tmp_path / "knowledge.db", embedder=emb, clock=clock)
    assert again.ambient is False                                                    # survives a restart


def test_who_was_there(mem):
    k, bus, emb, clock, _ = mem
    bus.emit(EventType.PERSON_IDENTIFIED, person_id="p1", display_name="Samuele")
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="domani porto il mio portatile nuovo", final=True)
    k.embed_pending()
    assert any("Samuele there" in line for line in k.recall("portatile nuovo"))


def test_recent_observations_are_recalled_before_any_sleep(mem):
    k, bus, emb, clock, _ = mem
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="il meeting con Luca è alle tre", final=True)
    clock.t += 3600
    bus.emit(EventType.SCENE_ANALYZED, description="A desk with a red mug and a laptop")
    k.embed_pending()
    clock.t += 600
    lines = k.recall("quando è il meeting con Luca?")
    assert "meeting con Luca" in lines[0] and "heard 1 h ago" in lines[0]


def test_sleep_turns_the_day_into_facts_and_merges_repeats(mem):
    k, bus, emb, clock, replies = mem
    for text in ("Samuele beve il caffè alle nove", "la stampante si inceppa il lunedì"):
        bus.emit(EventType.SPEECH_TRANSCRIBED, text=text + " come sempre", final=True)
    replies.append(json.dumps({"facts": [
        {"about": "Samuele", "fact": "Samuele drinks coffee at nine every morning", "confidence": 0.7},
        {"about": "place", "fact": "The printer jams on Mondays", "confidence": 0.6}]}))
    assert k.consolidate() == 2 and k.stats()["pending"] == 0
    replies.append(json.dumps({"facts": [
        {"about": "Samuele", "fact": "Samuele drinks coffee at nine every morning", "confidence": 0.6}]}))
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="Samuele di nuovo col caffè alle nove", final=True)
    k.consolidate()
    facts = {f["text"]: f for f in k.facts()}
    assert len(facts) == 2
    coffee = facts["Samuele drinks coffee at nine every morning"]
    assert coffee["count"] == 2 and coffee["confidence"] == pytest.approx(0.7)    # 0.5 at first, +0.2
    assert facts["The printer jams on Mondays"]["confidence"] == pytest.approx(0.5) # seen once: a guess
    assert k.recall("when does Samuele drink coffee every morning?")[0].startswith("[known] Samuele drinks coffee")


def test_raw_text_goes_once_learnt(mem):
    k, bus, emb, clock, replies = mem
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="una frase qualunque detta in ufficio", final=True)
    k.consolidate()
    clock.t += 49 * 3600
    k.consolidate()
    with k._lock:
        assert k._db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 0


def test_with_the_mac_away_nothing_is_lost(mem):
    k, bus, emb, clock, replies = mem
    emb.offline = True
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="il pacco arriva giovedì mattina", final=True)
    assert k.embed_pending() == 0
    replies.append("not json")
    assert k.consolidate() == 0 and k.stats()["pending"] == 1                       # postponed
    assert any("pacco" in line for line in k.recall("pacco"))                          # still recent
    emb.offline = False
    assert k.embed_pending() == 1


def test_the_same_view_is_not_kept_twice(mem):
    k, bus, emb, clock, _ = mem
    bus.emit(EventType.SCENE_ANALYZED, description="A desk with a red mug and a laptop")
    bus.emit(EventType.SCENE_ANALYZED, description="A laptop and a red mug with a desk")   # same view, other words
    bus.emit(EventType.SCENE_ANALYZED, description="Two people talking near the window")
    k.embed_pending()
    with k._lock:
        assert k._db.execute("SELECT COUNT(*) FROM observations").fetchone()[0] == 2


def test_forgetting_a_person(mem):
    k, bus, emb, clock, replies = mem
    bus.emit(EventType.PERSON_IDENTIFIED, person_id="p1", display_name="Luca")
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="Luca va in ferie ad agosto", final=True)
    replies.append(json.dumps({"facts": [
        {"about": "Luca", "fact": "Luca goes on holiday in August", "confidence": 0.8},
        {"about": "place", "fact": "The office closes at seven", "confidence": 0.8}]}))
    k.consolidate()
    k.forget_person("Luca")
    assert [f["text"] for f in k.facts()] == ["The office closes at seven"]
    assert not any("Luca" in line for line in k.recall("Luca ferie agosto"))


def test_its_own_voice_and_motors_are_not_remembered(tmp_path):
    clock, bus, emb = Clock(), Bus(), BagOfWords()
    moving = [False]
    k = KnowledgeService(bus, tmp_path / "k.db", embedder=emb, clock=clock, moving_fn=lambda: moving[0])
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="hello I am spooky your desk companion", final=True, own=True)
    moving[0] = True
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="tra tra tra tra", final=True)
    moving[0] = False
    bus.emit(EventType.SPEECH_TRANSCRIBED, text="il caffè è pronto in cucina", final=True)
    assert k.stats()["pending"] == 1


def test_no_sleep_while_someone_talks_to_it(mem):
    k, bus, emb, clock, _ = mem
    k.request_consolidation()
    bus.emit(EventType.COMMAND_PARSED, command="come stai")
    assert k._due() is False
    clock.t += 61
    assert k._due() is True
