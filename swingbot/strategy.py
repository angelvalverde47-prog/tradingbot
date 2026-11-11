"""Reglas puras de la estrategia: sin red, sin bróker. Fáciles de probar."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import Config


@dataclass
class Bars:
    """Velas diarias de un valor, de la más antigua a la más reciente."""
    symbol: str
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]


@dataclass
class Ticket:
    symbol: str
    setup: str
    qty: int
    entry: float
    stop: float
    target: float
    atr: float
    dollar_volume: float

    @property
    def risk_per_share(self) -> float:
        return round(self.entry - self.stop, 4)

    @property
    def cost(self) -> float:
        return round(self.qty * self.entry, 2)

    @property
    def max_loss(self) -> float:
        return round(self.qty * self.risk_per_share, 2)

    @property
    def reward_risk(self) -> float:
        return (self.target - self.entry) / self.risk_per_share


def atr(b: Bars, n: int = 14) -> float | None:
    if len(b.close) < n + 1:
        return None
    trs = []
    for i in range(1, len(b.close)):
        trs.append(max(b.high[i] - b.low[i], abs(b.high[i] - b.close[i - 1]), abs(b.low[i] - b.close[i - 1])))
    # media de Wilder
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def sma(xs: list[float], n: int) -> float:
    return sum(xs[-n:]) / n


def setups_for(b: Bars) -> list[str]:
    c = b.close
    found = []
    if len(c) < 22:
        return found
    if c[-1] > max(c[-21:-1]):
        found.append("breakout_20d")
    if len(c) >= 16:
        base, flag = c[-16], c[-6]
        last5 = c[-5:]
        if base > 0 and (flag - base) / base > 0.10 and (max(last5) - min(last5)) / max(last5) <= 0.05:
            found.append("bull_flag")
    prev_ma = sum(c[-21:-1]) / 20
    if c[-2] < prev_ma and c[-1] > sma(c, 20):
        found.append("reclaim_ma20")
    return found


def avg_dollar_volume(b: Bars, n: int = 20) -> float:
    pairs = list(zip(b.close[-n:], b.volume[-n:]))
    return sum(p * v for p, v in pairs) / len(pairs) if pairs else 0.0


def make_ticket(b: Bars, cfg: Config, cash_available: float) -> tuple[Ticket | None, str]:
    """Devuelve (ficha, "") o (None, motivo del descarte)."""
    if len(b.close) < 30:
        return None, "menos de 30 días de datos"
    entry = round(b.close[-1], 2)
    if not (cfg.min_price_usd <= entry <= cfg.max_price_usd):
        return None, f"precio {entry} fuera de rango"
    jump = (b.close[-1] / b.close[-2] - 1) * 100
    if jump > cfg.max_one_day_jump_pct:
        return None, f"subida de {jump:.0f}% en un día"
    dv = avg_dollar_volume(b)
    if dv < cfg.min_dollar_volume_usd:
        return None, "poca liquidez"
    setups = [s for s in setups_for(b) if s in cfg.setups]
    if not setups:
        return None, "sin setup"
    a = atr(b)
    if not a or a <= 0:
        return None, "sin ATR"
    stop = math.floor((entry - cfg.stop_atr_mult * a) * 100) / 100
    if stop <= 0 or stop >= entry:
        return None, "stop no válido"
    risk = entry - stop
    target = round(entry + cfg.target_r * risk, 2)
    if (target - entry) / risk < cfg.min_reward_risk:
        return None, "beneficio/riesgo insuficiente"
    qty = min(math.floor(cfg.max_loss_usd / risk), math.floor(cash_available / entry))
    if qty < 1:
        return None, ("stop demasiado lejos para la pérdida máxima" if risk > cfg.max_loss_usd
                      else "no queda efectivo")
    t = Ticket(b.symbol, setups[0], qty, entry, stop, target, round(a, 4), dv)
    if t.cost < cfg.min_order_usd:
        return None, "orden por debajo del mínimo"
    return t, ""


def rank(tickets: list[Ticket]) -> list[Ticket]:
    """Orden mecánico: lo más negociado primero."""
    return sorted(tickets, key=lambda t: -t.dollar_volume)
