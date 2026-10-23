"""Replies spoken as they are written: whole sentences, in order, and a fallback when the Mac fails."""

import sys
import threading
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import services.llm_host as llm_host
from core.bus import EventType
from services.mind import _FALLBACK_IT, OllamaBrain, SentenceSplitter


def test_sentences_come_out_whole_and_short_ones_wait():
    s = SentenceSplitter()
    out = []
    for chunk in ["Oh! ", "I am ", "a small spider", ". It is", " five forty-five.", " Do you want ", "more"]:
        out += s.feed(chunk)
    assert out == ["Oh! I am a small spider.", "It is five forty-five."]
    assert s.flush() == ["Do you want more"]


def test_numbers_and_decimals_are_not_sentence_ends():
    s = SentenceSplitter()
    assert s.feed("The battery reads 8.2 volts right now, ") == []


class FakeClient:
    def __init__(self, chunks, fail_after=None):
        self.chunks, self.fail_after = chunks, fail_after

    def chat(self, stream=False, **kw):
        def gen():
            for i, c in enumerate(self.chunks):
                if self.fail_after is not None and i == self.fail_after:
                    raise ConnectionError("Mac asleep")
                yield SimpleNamespace(message=SimpleNamespace(content=c))
        return gen()


def brain():
    b = OllamaBrain.__new__(OllamaBrain)
    b._cfg, b._ok, b._model, b._lock, b._history = {}, True, "gemma3:12b", threading.Lock(), []
    b._max_history, b._temp, b._max_tok, b._repeat_pen, b._keep_alive = 10, 0.7, 120, 1.1, 120
    return b


def test_a_streamed_reply_is_handed_over_sentence_by_sentence(monkeypatch):
    monkeypatch.setattr(llm_host, "client", lambda: FakeClient(["I am fine, ", "thank you. ", "Are you? 😊"]))
    said = []
    reply = brain().think("come stai?", on_sentence=said.append)
    assert said == ["I am fine, thank you.", "Are you?"] and reply == "I am fine, thank you. Are you?"


def test_the_mac_failing_before_a_word_still_gets_a_reply_said(monkeypatch):
    monkeypatch.setattr(llm_host, "client", lambda: FakeClient(["never"], fail_after=0))
    said = []
    reply = brain().think("come stai?", on_sentence=said.append)
    assert reply in _FALLBACK_IT and said == [reply]


def test_a_stream_cut_short_keeps_what_was_said(monkeypatch):
    monkeypatch.setattr(llm_host, "client", lambda: FakeClient(["I am fine today. ", "And then"], fail_after=1))
    said = []
    reply = brain().think("come stai?", on_sentence=said.append)
    assert said == ["I am fine today."] and reply == "I am fine today."


def test_the_voice_says_sentences_in_the_order_given():
    from services.audio import AudioOutput

    class Bus:
        def __init__(self):
            self.events = []

        def publish(self, kind, payload=None, source=""):
            self.events.append((kind, payload))

        def subscribe(self, *a):
            pass

    bus = Bus()
    out = AudioOutput(bus, method="silent")
    for i in range(6):
        out.say(f"Sentence {i}.", wait=False, continues=i > 0)
    out.say("Last.", wait=True, continues=True)
    started = [p for k, p in bus.events if k == EventType.TTS_STARTED]
    assert [p["text"] for p in started] == [f"Sentence {i}." for i in range(6)] + ["Last."]
    assert [p["continues"] for p in started] == [False] + [True] * 6


def test_the_chat_shows_one_reply_as_one_message():
    from services.dashboard import SharedState
    s = SharedState()
    s.add_chat("user", "come stai?")
    s.add_chat("spooky", "I am fine, thank you.")
    s.extend_chat("spooky", "Are you?")
    chat = s.snapshot()["chat_history"]
    assert [m["text"] for m in chat] == ["come stai?", "I am fine, thank you. Are you?"]
