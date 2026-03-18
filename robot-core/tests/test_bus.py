"""Tests for EventBus (core/bus.py)."""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from core.bus import EventBus, EventType, Event


def test_subscribe_and_publish_sync():
    bus = EventBus()
    received = []

    bus.subscribe(EventType.COMMAND_PARSED, lambda ev: received.append(ev))
    bus.publish_sync(EventType.COMMAND_PARSED, {"text": "hello"})

    assert len(received) == 1
    assert isinstance(received[0], Event)
    assert received[0].get("text") == "hello"


def test_unsubscribe():
    bus = EventBus()
    received = []

    def handler(ev):
        received.append(ev)

    bus.subscribe(EventType.COMMAND_PARSED, handler)
    bus.unsubscribe(EventType.COMMAND_PARSED, handler)
    bus.publish_sync(EventType.COMMAND_PARSED, {"text": "hello"})

    assert len(received) == 0


def test_multiple_subscribers():
    bus = EventBus()
    results = []

    bus.subscribe(EventType.MODE_CHANGED, lambda ev: results.append("a"))
    bus.subscribe(EventType.MODE_CHANGED, lambda ev: results.append("b"))
    bus.publish_sync(EventType.MODE_CHANGED, {})

    assert sorted(results) == ["a", "b"]


def test_publish_async():
    bus = EventBus()
    bus.start()
    received = []

    bus.subscribe(EventType.PERSON_DETECTED, lambda ev: received.append(ev))
    bus.publish(EventType.PERSON_DETECTED, {"name": "Marco"})

    for _ in range(20):
        if received:
            break
        time.sleep(0.05)

    bus.stop()
    assert len(received) == 1
    assert received[0].get("name") == "Marco"


def test_event_type_constants():
    assert hasattr(EventType, "COMMAND_PARSED")
    assert hasattr(EventType, "PERSON_DETECTED")
    assert hasattr(EventType, "MODE_CHANGED")
    assert hasattr(EventType, "OBSTACLE_DETECTED")
    assert hasattr(EventType, "ALERT_RAISED")


def test_event_get_default():
    ev = Event(type="test", payload={"a": 1})
    assert ev.get("a") == 1
    assert ev.get("missing", "default") == "default"


if __name__ == "__main__":
    test_subscribe_and_publish_sync()
    test_unsubscribe()
    test_multiple_subscribers()
    test_publish_async()
    test_event_type_constants()
    test_event_get_default()
    print("✅  test_bus: all passed")
