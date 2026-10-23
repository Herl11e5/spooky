"""
services/llm_host.py — The one Ollama connection, with timeouts.

The models run on the Mac (OLLAMA_HOST). The ollama library's default client
waits forever: a Mac that goes to sleep in the middle of a reply would hang the
mind for good. This client gives up on connecting after a few seconds (the Mac is
away: fall back at once) and on a reply after two minutes (a cold 12B model
takes a while to load).
"""

from __future__ import annotations

import threading
from typing import Optional

_CONNECT_S = 5.0
_REPLY_S = 120.0

_client = None
_lock = threading.Lock()


def client():
    """A shared ollama.Client honouring OLLAMA_HOST, with connect and read timeouts."""
    global _client
    with _lock:
        if _client is None:
            import httpx
            import ollama
            _client = ollama.Client(timeout=httpx.Timeout(_REPLY_S, connect=_CONNECT_S))
        return _client


def available_models() -> Optional[list]:
    """Model names on the host, or None when it cannot be reached."""
    try:
        return [m.model for m in client().list().models]
    except Exception:
        return None
