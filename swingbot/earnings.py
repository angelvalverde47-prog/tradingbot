"""Fechas de resultados desde el calendario público de Nasdaq (sin clave).

Si el calendario no responde, el bot NO abre operaciones nuevas ese día:
mejor no operar que abrir una posición con resultados encima.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from datetime import date, timedelta

log = logging.getLogger("swingbot.earnings")
URL = "https://api.nasdaq.com/api/calendar/earnings?date={d}"
HEADERS = {"User-Agent": "Mozilla/5.0 (swingbot paper test)", "Accept": "application/json"}


def _fetch_day(d: date) -> list[str]:
    req = urllib.request.Request(URL.format(d=d.isoformat()), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.load(r)
    rows = ((data or {}).get("data") or {}).get("rows") or []
    return [row["symbol"].upper() for row in rows if row.get("symbol")]


def upcoming(days: int, start: date | None = None) -> dict[str, date] | None:
    """{símbolo: primera fecha de resultados} en los próximos `days` días naturales.
    Devuelve None si no se ha podido leer el calendario."""
    start = start or date.today()
    out: dict[str, date] = {}
    failed = 0
    weekdays = 0
    for i in range(days + 1):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        weekdays += 1
        try:
            for s in _fetch_day(d):
                out.setdefault(s, d)
        except Exception as e:  # noqa: BLE001
            failed += 1
            log.warning("calendario de resultados %s: %s", d, e)
        time.sleep(0.2)
    if weekdays and failed > weekdays // 3:
        return None
    return out
