"""Simulador de cartera día a día, con las mismas mecánicas que el bot en vivo:

- Por la mañana (antes de la apertura) se ponen compras LIMITADAS al cierre del día anterior.
- Una compra se ejecuta si el mínimo del día toca el límite (al límite, o a la apertura si abre por debajo).
- La compra que no se ejecuta en `entry_ttl_days` días de mercado se cancela.
- Stop y objetivo: si el día abre por debajo del stop o por encima del objetivo, sale a la apertura (hueco).
  Si en un mismo día se tocan los dos, se asume el STOP (supuesto pesimista).
- El día de la compra solo se mira el stop (no se sabe si el objetivo llegó antes o después).
- Salida por tiempo: a la apertura del día en que la posición cumple `max_hold_days`.
- No se modelan los resultados empresariales (no hay calendario histórico gratuito): eso hace el
  backtest algo pesimista en saltos, porque el bot real evita esos días.
- Coste: 0,01 $ por acción en cada ejecución (deslizamiento); Alpaca no cobra comisión.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

SLIP = 0.01  # $ por acción


@dataclass(frozen=True)
class Params:
    setups: tuple = ("breakout_20d", "bull_flag", "reclaim_ma20")
    stop_atr_mult: float = 1.5
    target_r: float = 2.0
    max_hold_days: int = 15
    max_positions: int = 2
    min_price_usd: float = 5.0
    max_price_usd: float = 45.0
    min_dollar_volume_usd: float = 10_000_000
    max_one_day_jump_pct: float = 50.0
    entry_ttl_days: int = 2
    risk_pct: float = 1.0          # % del capital por operación
    cash_buffer_pct: float = 10.0
    min_order_usd: float = 5.55

    def label(self) -> str:
        s = "+".join({"breakout_20d": "BO", "bull_flag": "FL", "reclaim_ma20": "MA"}[x] for x in self.setups)
        return (f"{s} stop{self.stop_atr_mult}xATR obj{self.target_r}R {self.max_hold_days}d "
                f"{self.max_positions}pos ≤{self.max_price_usd:g}$")


def candidates(sig: dict, close: np.ndarray, p: Params) -> list[np.ndarray]:
    """Por cada día t, índices de los valores con señal, ordenados por volumen negociado (desc)."""
    mask = np.zeros_like(sig["breakout_20d"], dtype=bool)
    for s in p.setups:
        mask |= sig[s]
    with np.errstate(invalid="ignore"):
        mask &= (close >= p.min_price_usd) & (close <= p.max_price_usd)
        mask &= sig["dv20"] >= p.min_dollar_volume_usd
        mask &= ~(sig["jump"] > p.max_one_day_jump_pct)
        mask &= sig["atr"] > 0
    out = []
    dv = np.nan_to_num(sig["dv20"])
    for t in range(mask.shape[0]):
        idx = np.flatnonzero(mask[t])
        out.append(idx[np.argsort(-dv[t, idx])] if len(idx) else idx)
    return out


def run(bars: dict, sig: dict, p: Params, start: int, end: int, capital: float,
        cands: list | None = None) -> dict:
    """Simula los días [start, end). Devuelve curva de capital y operaciones."""
    O, H, L, C = bars["open"], bars["high"], bars["low"], bars["close"]
    atr = sig["atr"]
    cands = cands if cands is not None else candidates(sig, C, p)
    cash = capital
    realized = 0.0
    pending = []   # dicts: j, qty, limit, stop, target, age
    open_ = []     # dicts: j, qty, entry, stop, target, day
    trades = []
    equity = np.zeros(end - start)

    for t in range(start, end):
        # 1) compras pendientes (puestas antes de la apertura de t)
        still = []
        for o in pending:
            j = o["j"]
            lo, op = float(L[t, j]), float(O[t, j])
            if not np.isnan(lo) and lo <= o["limit"]:
                fill = min(op, o["limit"]) + SLIP
                cash -= o["qty"] * fill
                pos = {**o, "entry": fill, "day": t}
                if lo <= o["stop"]:  # el stop salta el mismo día
                    px = min(o["stop"], op) - SLIP
                    cash += o["qty"] * px
                    trades.append(_close(pos, px, "stop_loss", t))
                    realized += trades[-1]["pnl"]
                else:
                    open_.append(pos)
            else:
                o["age"] += 1
                if o["age"] < p.entry_ttl_days:
                    still.append(o)
        pending = still

        # 2) posiciones abiertas antes de hoy
        keep = []
        for pos in open_:
            if pos["day"] == t:
                keep.append(pos)
                continue
            j = pos["j"]
            op, hi, lo = float(O[t, j]), float(H[t, j]), float(L[t, j])
            if np.isnan(op):
                keep.append(pos)
                continue
            px = reason = None
            if t - pos["day"] >= p.max_hold_days:
                px, reason = op, "time"
            elif op <= pos["stop"]:
                px, reason = op, "stop_loss"
            elif op >= pos["target"]:
                px, reason = op, "take_profit"
            elif lo <= pos["stop"]:
                px, reason = pos["stop"], "stop_loss"
            elif hi >= pos["target"]:
                px, reason = pos["target"], "take_profit"
            if px is None:
                keep.append(pos)
                continue
            px -= SLIP
            cash += pos["qty"] * px
            trades.append(_close(pos, px, reason, t))
            realized += trades[-1]["pnl"]
        open_ = keep

        # 3) valor de la cartera al cierre
        mark = sum(pos["qty"] * (float(C[t, pos["j"]]) if not np.isnan(C[t, pos["j"]]) else pos["entry"]) for pos in open_)
        equity[t - start] = cash + mark

        # 4) órdenes para mañana, con la información del cierre de hoy
        if t + 1 >= end:
            continue
        slots = p.max_positions - len(open_) - len(pending)
        if slots <= 0:
            continue
        base = capital + realized                 # capital para el tamaño: inicial + resultado cerrado
        risk_usd = base * p.risk_pct / 100
        committed = sum(x["qty"] * x["entry"] for x in open_) + sum(x["qty"] * x["limit"] for x in pending)
        avail = min(base * (1 - p.cash_buffer_pct / 100) - committed, cash - sum(x["qty"] * x["limit"] for x in pending))
        busy = {x["j"] for x in open_} | {x["j"] for x in pending}
        for j in cands[t]:
            if slots <= 0 or avail < p.min_order_usd:
                break
            if j in busy:
                continue
            entry = round(float(C[t, j]), 2)
            stop = math.floor((entry - p.stop_atr_mult * float(atr[t, j])) * 100) / 100
            if stop <= 0 or stop >= entry:
                continue
            rps = entry - stop
            qty = min(math.floor(risk_usd / rps), math.floor(avail / entry))
            if qty < 1 or qty * entry < p.min_order_usd:
                continue
            target = round(entry + p.target_r * rps, 2)
            pending.append({"j": int(j), "qty": qty, "limit": entry, "stop": stop, "target": target, "age": 0})
            avail -= qty * entry
            slots -= 1
            busy.add(j)

    return {"equity": equity, "trades": trades, "capital": capital}


def _close(pos, px, reason, t):
    pnl = (px - pos["entry"]) * pos["qty"]
    risk = (pos["limit"] - pos["stop"]) * pos["qty"]  # riesgo planeado (el de la ficha)
    return {"j": pos["j"], "qty": pos["qty"], "entry": pos["entry"], "exit": px, "reason": reason,
            "day_in": pos["day"], "day_out": t, "pnl": pnl, "r": pnl / risk if risk > 0 else 0.0}


def metrics(res: dict, days_per_year: float = 252) -> dict:
    eq = res["equity"]
    cap = res["capital"]
    tr = res["trades"]
    if len(eq) == 0:
        return {}
    final = float(eq[-1])
    years = len(eq) / days_per_year
    cagr = (final / cap) ** (1 / years) - 1 if final > 0 and years > 0 else -1.0
    peak = np.maximum.accumulate(np.concatenate([[cap], eq]))
    dd = float(np.max(1 - np.concatenate([[cap], eq]) / peak))
    rets = np.diff(np.concatenate([[cap], eq])) / np.concatenate([[cap], eq])[:-1]
    sharpe = float(np.mean(rets) / np.std(rets) * np.sqrt(days_per_year)) if np.std(rets) > 0 else 0.0
    wins = [x for x in tr if x["pnl"] > 0]
    gross_win = sum(x["pnl"] for x in wins)
    gross_loss = -sum(x["pnl"] for x in tr if x["pnl"] <= 0)
    return {
        "final": final, "return_pct": 100 * (final / cap - 1), "cagr_pct": 100 * cagr, "max_dd_pct": 100 * dd,
        "sharpe": sharpe, "trades": len(tr), "win_rate_pct": 100 * len(wins) / len(tr) if tr else 0.0,
        "avg_r": float(np.mean([x["r"] for x in tr])) if tr else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0),
        "mar": (100 * cagr) / (100 * dd) if dd > 0 else 0.0,
    }
