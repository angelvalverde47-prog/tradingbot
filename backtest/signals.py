"""Las mismas reglas que swingbot/strategy.py, pero calculadas para todos los días y valores a la vez.
tests/test_backtest.py comprueba que dan el mismo resultado que el bot en vivo."""
from __future__ import annotations

import numpy as np


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    """a desplazado k filas hacia abajo (a[t-k] en la fila t)."""
    out = np.full_like(a, np.nan)
    out[k:] = a[:-k]
    return out


def _rolling(a: np.ndarray, n: int, fn) -> np.ndarray:
    """fn sobre la ventana a[t-n+1..t]; NaN si falta algún dato."""
    out = np.full_like(a, np.nan)
    if len(a) < n:
        return out
    w = np.lib.stride_tricks.sliding_window_view(a, n, axis=0)  # (T-n+1, N, n)
    with np.errstate(all="ignore"):
        out[n - 1:] = fn(w, axis=-1)
    return out


def atr_wilder(high, low, close, n: int = 14) -> np.ndarray:
    pc = _shift(close, 1)
    tr = np.fmax(high - low, np.fmax(np.abs(high - pc), np.abs(low - pc)))
    tr[0] = np.nan
    T, N = close.shape
    out = np.full((T, N), np.nan, dtype=np.float64)
    a = np.full(N, np.nan)
    count = np.zeros(N, dtype=int)
    acc = np.zeros(N)
    for t in range(1, T):
        x = tr[t].astype(np.float64)
        ok = ~np.isnan(x)
        # semilla: media simple de los n primeros TR; después, media de Wilder
        seeding = ok & (count < n)
        acc[seeding] += x[seeding]
        count[seeding] += 1
        just = seeding & (count == n)
        a[just] = acc[just] / n
        roll = ok & ~seeding & ~np.isnan(a)
        a[roll] = (a[roll] * (n - 1) + x[roll]) / n
        a[~ok] = np.nan          # hueco en los datos: se reinicia
        count[~ok] = 0
        acc[~ok] = 0
        out[t] = a
    return out


def compute(close, high, low, volume) -> dict:
    c = close.astype(np.float64)
    prev = _shift(c, 1)
    prev_max20 = _shift(_rolling(c, 20, np.max), 1)                 # máx. de c[t-20..t-1]
    breakout = c > prev_max20
    base, flag = _shift(c, 15), _shift(c, 5)
    mx5, mn5 = _rolling(c, 5, np.max), _rolling(c, 5, np.min)
    with np.errstate(all="ignore"):
        bull_flag = ((flag - base) / base > 0.10) & ((mx5 - mn5) / mx5 <= 0.05)
    ma20 = _rolling(c, 20, np.mean)
    reclaim = (prev < _shift(ma20, 1)) & (c > ma20)
    dv20 = _rolling(c * volume.astype(np.float64), 20, np.mean)
    with np.errstate(all="ignore"):
        jump = (c / prev - 1) * 100
    enough = _shift(c, 29)  # el bot pide al menos 30 días de datos
    valid = ~np.isnan(enough) & ~np.isnan(c)
    return {
        "breakout_20d": breakout & valid,
        "bull_flag": bull_flag & valid,
        "reclaim_ma20": reclaim & valid,
        "atr": atr_wilder(high.astype(np.float64), low.astype(np.float64), c),
        "dv20": dv20,
        "jump": jump,
    }
