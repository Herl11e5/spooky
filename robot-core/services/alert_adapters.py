"""
services/alert_adapters.py — External alert sink adapters.

All adapters are optional. Missing dependencies → warning, no crash.
Each adapter is registered with AlertService.register_adapter().

Available adapters:
  TelegramAdapter      — sends a message via a Telegram bot
  WebhookAdapter       — HTTP POST JSON to any URL
  HomeAssistantAdapter — fires a HA event via the REST API

Configuration (robot.yaml):
  alerts:
    telegram:
      enabled: true
      token: "123456:ABC..."
      chat_id: "-100123456"
    webhook:
      enabled: true
      url: "http://192.168.1.10:8080/spooky/alert"
    home_assistant:
      enabled: false
      url: "http://homeassistant.local:8123"
      token: "your_long_lived_token"
      event_type: "spooky_alert"

Factory function:
  build_adapters(cfg) → List[AlertAdapter]
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from services.alert import AlertAdapter

log = logging.getLogger(__name__)


# ── Telegram ──────────────────────────────────────────────────────────────────

class TelegramAdapter(AlertAdapter):
    """
    Sends alert messages via a Telegram bot.
    Requires: pip install requests   (standard on most systems)
    Bot setup: @BotFather → create bot → get token + chat_id.
    """

    _API = "https://api.telegram.org/bot{token}/sendMessage"
    _LEVEL_EMOJI = {1: "👁️", 2: "⚠️", 3: "🚨"}

    def __init__(self, token: str, chat_id: str, timeout_s: float = 8.0):
        self._token   = token
        self._chat_id = str(chat_id)
        self._timeout = timeout_s

    def send(self, level: int, reason: str, payload: Dict[str, Any]) -> None:
        emoji = self._LEVEL_EMOJI.get(level, "🔔")
        ts    = time.strftime("%H:%M:%S", time.localtime(payload.get("ts", time.time())))
        text  = (
            f"{emoji} *Spooky Night Watch* — L{level}\n"
            f"🕐 {ts}\n"
            f"📋 {reason.replace('_', ' ')}"
        )
        confidence = payload.get("confidence")
        if confidence is not None:
            text += f"\n🎯 Confidenza: {confidence:.0%}"

        try:
            import requests
            resp = requests.post(
                self._API.format(token=self._token),
                json={"chat_id": self._chat_id, "text": text, "parse_mode": "Markdown"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            log.info(f"TelegramAdapter: sent L{level} alert")
        except ImportError:
            log.warning("TelegramAdapter: 'requests' not installed — pip install requests")
        except Exception as e:
            log.error(f"TelegramAdapter: send failed — {e}")


# ── Webhook ───────────────────────────────────────────────────────────────────

class WebhookAdapter(AlertAdapter):
    """
    HTTP POST JSON payload to a configured URL.
    Works with any webhook receiver (n8n, Zapier, custom server, etc.).
    """

    def __init__(self, url: str, timeout_s: float = 5.0, headers: Optional[Dict] = None):
        self._url     = url
        self._timeout = timeout_s
        self._headers = headers or {"Content-Type": "application/json"}

    def send(self, level: int, reason: str, payload: Dict[str, Any]) -> None:
        body = {
            "source":    "spooky",
            "level":     level,
            "reason":    reason,
            "timestamp": time.time(),
            **payload,
        }
        try:
            import requests
            resp = requests.post(
                self._url, json=body, headers=self._headers, timeout=self._timeout
            )
            resp.raise_for_status()
            log.info(f"WebhookAdapter: sent L{level} alert → {self._url}")
        except ImportError:
            log.warning("WebhookAdapter: 'requests' not installed — pip install requests")
        except Exception as e:
            log.error(f"WebhookAdapter: {e}")


# ── Home Assistant ────────────────────────────────────────────────────────────

class HomeAssistantAdapter(AlertAdapter):
    """
    Fires a Home Assistant event via the HA REST API.
    In HA you can then trigger automations on event_type = spooky_alert.

    Requires: long-lived access token from your HA profile.
    """

    def __init__(
        self,
        url: str,
        token: str,
        event_type: str = "spooky_alert",
        timeout_s: float = 5.0,
    ):
        self._url        = url.rstrip("/")
        self._token      = token
        self._event_type = event_type
        self._timeout    = timeout_s

    def send(self, level: int, reason: str, payload: Dict[str, Any]) -> None:
        endpoint = f"{self._url}/api/events/{self._event_type}"
        body = {"level": level, "reason": reason, "timestamp": time.time(), **payload}
        try:
            import requests
            resp = requests.post(
                endpoint,
                json=body,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            log.info(f"HomeAssistantAdapter: fired '{self._event_type}' L{level}")
        except ImportError:
            log.warning("HomeAssistantAdapter: 'requests' not installed")
        except Exception as e:
            log.error(f"HomeAssistantAdapter: {e}")


# ── Factory ───────────────────────────────────────────────────────────────────

def build_adapters(cfg) -> List[AlertAdapter]:
    """
    Read config and instantiate all enabled adapters.
    Call once at startup; pass result to AlertService.register_adapter().
    """
    adapters: List[AlertAdapter] = []
    alert_cfg = cfg.get("alerts") or {}

    # Telegram
    tg = alert_cfg.get("telegram") or {}
    if tg.get("enabled") and tg.get("token") and tg.get("chat_id"):
        adapters.append(TelegramAdapter(tg["token"], str(tg["chat_id"])))
        log.info("AlertAdapter: Telegram enabled")

    # Webhook
    wh = alert_cfg.get("webhook") or {}
    if wh.get("enabled") and wh.get("url"):
        adapters.append(WebhookAdapter(wh["url"]))
        log.info(f"AlertAdapter: Webhook enabled → {wh['url']}")

    # Home Assistant
    ha = alert_cfg.get("home_assistant") or {}
    if ha.get("enabled") and ha.get("url") and ha.get("token"):
        adapters.append(HomeAssistantAdapter(
            ha["url"], ha["token"], ha.get("event_type", "spooky_alert")
        ))
        log.info("AlertAdapter: HomeAssistant enabled")

    if not adapters:
        log.info("AlertAdapters: no external adapters configured (all L3 alerts logged only)")

    return adapters
