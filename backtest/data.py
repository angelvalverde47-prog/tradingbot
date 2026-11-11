"""Descarga velas diarias ajustadas de Alpaca para todo el universo y las guarda en un .npz.

Uso: python -m backtest.data --start 2020-09-01 --out backtest/cache/bars.npz
Necesita ALPACA_API_KEY y ALPACA_SECRET_KEY (las de paper sirven para los datos).

Aviso: el universo son las acciones activas HOY (sesgo de supervivencia: faltan las que
quebraron o dejaron de cotizar). Eso hace que el backtest sea algo optimista.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
ASSETS_URL = "https://paper-api.alpaca.markets/v2/assets?status=active&asset_class=us_equity"
EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS"}
FUND_WORDS = ("ETF", "ETN", " FUND", "ISHARES", "PROSHARES", "DIREXION", "LEVERAGED", " 2X", " 3X",
              "ULTRA", "WARRANT", "RIGHT", " UNIT", "PREFERRED", "DEPOSITARY SHARES REP")


def _headers():
    return {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
            "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"],
            "Accept": "application/json"}


def _get(url: str, tries: int = 6):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=_headers()), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:
                time.sleep(3 * (i + 1))
                continue
            raise
        except Exception:  # noqa: BLE001
            time.sleep(3 * (i + 1))
    raise RuntimeError(f"no se pudo leer {url[:120]}")


def universe() -> list[str]:
    out = []
    for a in _get(ASSETS_URL):
        name = (a.get("name") or "").upper()
        sym = a.get("symbol") or ""
        if not a.get("tradable") or a.get("exchange") not in EXCHANGES:
            continue
        if not sym.isalpha() or len(sym) > 5 or any(w in name for w in FUND_WORDS):
            continue
        out.append(sym)
    return sorted(out)


def download(symbols: list[str], start: date, end: date, feed: str = "sip", batch: int = 100) -> dict:
    """{símbolo: lista de (día, o, h, l, c, v)}"""
    out: dict[str, list] = {}
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        token = None
        while True:
            q = {"symbols": ",".join(chunk), "timeframe": "1Day", "start": start.isoformat(),
                 "end": end.isoformat(), "adjustment": "all", "feed": feed, "limit": 10000}
            if token:
                q["page_token"] = token
            data = _get(DATA_URL + "?" + urllib.parse.urlencode(q))
            for sym, bars in (data.get("bars") or {}).items():
                rows = out.setdefault(sym, [])
                for b in bars:
                    rows.append((b["t"][:10], b["o"], b["h"], b["l"], b["c"], b["v"]))
            token = data.get("next_page_token")
            if not token:
                break
        print(f"  {min(i + batch, len(symbols))}/{len(symbols)} valores", flush=True)
    return out


def to_arrays(raw: dict, min_dollar_volume: float) -> dict:
    days = sorted({r[0] for rows in raw.values() for r in rows})
    di = {d: i for i, d in enumerate(days)}
    syms = sorted(raw)
    T, N = len(days), len(syms)
    arr = {k: np.full((T, N), np.nan, dtype=np.float32) for k in ("open", "high", "low", "close", "volume")}
    for j, s in enumerate(syms):
        for d, o, h, lo, c, v in raw[s]:
            i = di[d]
            arr["open"][i, j], arr["high"][i, j], arr["low"][i, j] = o, h, lo
            arr["close"][i, j], arr["volume"][i, j] = c, v
    # quitar columnas que nunca llegan a la liquidez mínima (ahorra memoria y tiempo)
    dv = arr["close"] * arr["volume"]
    keep = np.nanmax(dv, axis=0) >= min_dollar_volume
    out = {k: v[:, keep] for k, v in arr.items()}
    out["days"] = np.array(days)
    out["symbols"] = np.array(syms)[keep]
    return out


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2020-09-01")
    p.add_argument("--end", default=None)
    p.add_argument("--feed", default="sip")
    p.add_argument("--out", default="backtest/cache/bars.npz")
    p.add_argument("--min-dollar-volume", type=float, default=2_000_000)
    a = p.parse_args(argv)
    end = date.fromisoformat(a.end) if a.end else (datetime.now(timezone.utc) - timedelta(days=1)).date()
    syms = universe()
    print(f"universo: {len(syms)} acciones", flush=True)
    raw = download(syms, date.fromisoformat(a.start), end, a.feed)
    for spy in ("SPY",):  # referencia: comprar y mantener el S&P 500
        raw.update(download([spy], date.fromisoformat(a.start), end, a.feed))
    arrs = to_arrays(raw, a.min_dollar_volume)
    spy = raw.get("SPY", [])
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(a.out, **arrs, spy_days=np.array([r[0] for r in spy]),
                        spy_close=np.array([r[4] for r in spy], dtype=np.float64))
    print(f"guardado {a.out}: {len(arrs['days'])} días x {len(arrs['symbols'])} valores", flush=True)


if __name__ == "__main__":
    main()
