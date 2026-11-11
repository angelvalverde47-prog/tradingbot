"""Avisos al móvil. Opcional: ntfy (NTFY_TOPIC) y/o Telegram (TELEGRAM_TOKEN + TELEGRAM_CHAT_ID).
Sin ninguno configurado, los mensajes solo van al log."""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

log = logging.getLogger("swingbot.notify")


def _ascii(s: str) -> str:
    """Las cabeceras HTTP no admiten tildes: 'Resumen día' -> 'Resumen dia'."""
    import unicodedata
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def send(title: str, body: str) -> None:
    log.info("%s\n%s", title, body)
    topic = os.environ.get("NTFY_TOPIC")
    if topic:
        server = os.environ.get("NTFY_SERVER", "https://ntfy.sh").rstrip("/")
        try:
            req = urllib.request.Request(f"{server}/{topic}", data=body.encode("utf-8"),
                                         headers={"Title": _ascii(title), "Tags": "chart_with_upwards_trend"})
            urllib.request.urlopen(req, timeout=15).read()
        except Exception as e:  # noqa: BLE001
            log.warning("ntfy: %s", e)
    token, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if token and chat:
        try:
            data = urllib.parse.urlencode({"chat_id": chat, "text": f"{title}\n\n{body}"}).encode()
            urllib.request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=15).read()
        except Exception as e:  # noqa: BLE001
            log.warning("telegram: %s", e)
