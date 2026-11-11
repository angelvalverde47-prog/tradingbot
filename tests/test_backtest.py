"""El backtest debe usar exactamente las mismas reglas que el bot en vivo."""
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import signals, sim  # noqa: E402
from swingbot.config import Config  # noqa: E402
from swingbot.strategy import Bars, atr, make_ticket, setups_for  # noqa: E402


def random_market(T=300, N=40, seed=1):
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0005, 0.02, (T, N))
    close = 10 * np.exp(np.cumsum(rets, axis=0))
    rngs = np.abs(rng.normal(0, 0.015, (T, N))) * close
    high, low = close + rngs / 2, close - rngs / 2
    open_ = close * (1 + rng.normal(0, 0.005, (T, N)))
    high = np.maximum(high, open_)
    low = np.minimum(low, open_)
    vol = rng.uniform(1e6, 5e6, (T, N))
    return {k: v.astype(np.float32) for k, v in
            dict(open=open_, high=high, low=low, close=close, volume=vol).items()}


def test_signals_match_live_strategy():
    m = random_market()
    s = signals.compute(m["close"], m["high"], m["low"], m["volume"])
    hits = 0
    for t in range(80, 300, 7):
        for j in range(40):
            win = slice(t - 59, t + 1)  # ~60 sesiones, como las que descarga el bot (90 días naturales)
            b = Bars("X", [float(x) for x in m["high"][win, j]], [float(x) for x in m["low"][win, j]],
                     [float(x) for x in m["close"][win, j]], [float(x) for x in m["volume"][win, j]])
            live = set(setups_for(b))
            back = {k for k in ("breakout_20d", "bull_flag", "reclaim_ma20") if s[k][t, j]}
            assert live == back, (t, j, live, back)
            hits += len(live)
            # ATR: el bot siembra con su ventana; tras ~45 pasos la diferencia es mínima
            assert abs(atr(b) - s["atr"][t, j]) / s["atr"][t, j] < 0.02
    assert hits > 20


def test_ticket_math_matches():
    m = random_market(seed=3)
    s = signals.compute(m["close"], m["high"], m["low"], m["volume"])
    cfg = Config()
    p = sim.Params(max_price_usd=1e9, min_dollar_volume_usd=0, risk_pct=100 * cfg.max_loss_usd / 111)
    for t in range(100, 300, 11):
        for j in range(40):
            win = slice(t - 59, t + 1)
            b = Bars("X", [float(x) for x in m["high"][win, j]], [float(x) for x in m["low"][win, j]],
                     [float(x) for x in m["close"][win, j]], [float(x) for x in m["volume"][win, j]])
            tk, _ = make_ticket(b, cfg, 1e9)
            if tk:
                entry = round(float(m["close"][t, j]), 2)
                stop = math.floor((entry - p.stop_atr_mult * float(s["atr"][t, j])) * 100) / 100
                assert abs(stop - tk.stop) <= 0.02


def test_sim_runs_and_respects_limits():
    m = random_market(T=400, N=60, seed=5)
    s = signals.compute(m["close"], m["high"], m["low"], m["volume"])
    p = sim.Params(max_positions=3, min_dollar_volume_usd=0, max_price_usd=1e9)
    res = sim.run(m, s, p, 60, 400, 111.0)
    met = sim.metrics(res)
    assert met["trades"] > 5
    # nunca más de 3 posiciones abiertas a la vez
    for t in range(60, 400):
        assert sum(1 for x in res["trades"] if x["day_in"] <= t < x["day_out"]) <= 3
    # sin huecos, una pérdida no pasa de -1R (más el deslizamiento); con huecos puede ser mayor
    assert np.median([x["r"] for x in res["trades"] if x["reason"] == "stop_loss"]) >= -1.2
    assert abs(res["equity"][-1] - (111 + sum(x["pnl"] for x in res["trades"]))) < 60


def test_gap_and_stop_first():
    # una sola acción, con una señal clara y luego un hueco bajista por debajo del stop
    T = 80
    c = np.full((T, 1), 20.0, dtype=np.float32)
    c[:61] += np.sin(np.arange(61))[:, None] * 0.05
    c[61] = 21.0          # ruptura de máximo de 20 días
    c[62:] = 21.0
    h, lo, o = c + 0.1, c - 0.1, c.copy()
    o[63], lo[63], h[63], c[63] = 18.0, 17.9, 18.2, 18.1   # al día siguiente de comprar abre muy abajo
    v = np.full((T, 1), 5e6, dtype=np.float32)
    bars = dict(open=o, high=h, low=lo, close=c, volume=v)
    s = signals.compute(c, h, lo, v)
    assert s["breakout_20d"][61, 0]
    res = sim.run(bars, s, sim.Params(min_dollar_volume_usd=0, risk_pct=10), 30, T, 111.0)
    tr = [x for x in res["trades"] if x["day_in"] == 62][0]
    assert tr["reason"] == "stop_loss" and abs(tr["exit"] - (18.0 - sim.SLIP)) < 1e-6
