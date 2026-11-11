"""Fechas de resultados.

Fuente principal: Finnhub (gratis, una sola petición para todo el rango) si hay FINNHUB_API_KEY.
Si no, el calendario público de Nasdaq (sin clave, un día por petición, en paralelo).

Si no se puede leer el calendario, el bot NO abre operaciones nuevas ese día:
mejor no operar que abrir una posición con resultados encima.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta

log = logging.getLogger("swingbot.earnings")

NASDAQ_URL = "https://api.nasdaq.com/api/calendar/earnings?date={d}"
NASDAQ_HEADERS = {  # Nasdaq deja colgadas las peticiones que no parecen de un navegador
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/130.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}
FINNHUB_URL = "https://finnhub.io/api/v1/calendar/earnings?"


def _get_json(url: str, headers: dict | None = None, timeout: int = 10):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": "swingbot"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _finnhub(start: date, end: date, key: str) -> dict[str, date]:
    q = urllib.parse.urlencode({"from": start.isoformat(), "to": end.isoformat(), "token": key})
    data = _get_json(FINNHUB_URL + q, timeout=20)
    out: dict[str, date] = {}
    for row in data.get("earningsCalendar") or []:
        sym, d = (row.get("symbol") or "").upper(), row.get("date")
        if sym and d:
            dd = date.fromisoformat(d)
            if sym not in out or dd < out[sym]:
                out[sym] = dd
    return out


def _nasdaq_day(d: date) -> list[str]:
    data = _get_json(NASDAQ_URL.format(d=d.isoformat()), NASDAQ_HEADERS, timeout=10)
    rows = ((data or {}).get("data") or {}).get("rows") or []
    return [row["symbol"].upper() for row in rows if row.get("symbol")]


def _nasdaq(days: list[date]) -> dict[str, date] | None:
    out: dict[str, date] = {}
    failed = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        results = list(ex.map(lambda d: (d, _try(_nasdaq_day, d)), days))
    for d, syms in sorted(results):
        if syms is None:
            failed += 1
            continue
        for s in syms:
            out.setdefault(s, d)
    if days and failed > len(days) // 3:
        return None
    return out


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception as e:  # noqa: BLE001
        log.warning("calendario de resultados %s: %s", a, e)
        return None


def upcoming(days: int, start: date | None = None) -> dict[str, date] | None:
    """{símbolo: primera fecha de resultados} en los próximos `days` días naturales.
    Devuelve None si no se ha podido leer el calendario."""
    start = start or date.today()
    end = start + timedelta(days=days)
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if key:
        try:
            return _finnhub(start, end, key)
        except Exception as e:  # noqa: BLE001
            log.warning("Finnhub: %s; pruebo con Nasdaq", e)
    weekdays = [start + timedelta(days=i) for i in range(days + 1) if (start + timedelta(days=i)).weekday() < 5]
    return _nasdaq(weekdays)
