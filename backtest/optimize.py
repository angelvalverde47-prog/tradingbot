"""Optimizador con validación fuera de muestra.

1. Entrenamiento (in-sample): se prueban todas las combinaciones de la rejilla.
2. Se elige la mejor SOLO con los datos de entrenamiento (Sharpe, con un mínimo de operaciones).
3. Se mide esa misma combinación en el periodo de validación, que el optimizador no ha visto.
   Ese resultado es la estimación honesta de lo que cabe esperar.

Uso: python -m backtest.optimize --bars backtest/cache/bars.npz --split 2024-07-01
"""
from __future__ import annotations

import argparse
import csv
import itertools
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backtest import signals, sim  # noqa: E402

G: dict = {}  # datos compartidos con los procesos hijos (fork)

SETUP_SETS = [("breakout_20d",), ("bull_flag",), ("reclaim_ma20",), ("breakout_20d", "bull_flag"),
              ("breakout_20d", "reclaim_ma20"), ("bull_flag", "reclaim_ma20"),
              ("breakout_20d", "bull_flag", "reclaim_ma20")]


def grid(risk_pct: float) -> list[sim.Params]:
    out = []
    for setups, m, r, hold, pos, pmax in itertools.product(
            SETUP_SETS, (1.0, 1.5, 2.0, 2.5), (1.5, 2.0, 3.0), (5, 10, 20), (2, 3, 5), (45.0, 90.0)):
        out.append(sim.Params(setups=setups, stop_atr_mult=m, target_r=r, max_hold_days=hold,
                              max_positions=pos, max_price_usd=pmax, risk_pct=risk_pct))
    return out


def _cands(p: sim.Params):
    key = (p.setups, p.max_price_usd, p.min_price_usd, p.min_dollar_volume_usd)
    cache = G.setdefault("cands", {})
    if key not in cache:
        cache[key] = sim.candidates(G["sig"], G["bars"]["close"], p)
    return cache[key]


def evaluate(p: sim.Params) -> dict:
    c = _cands(p)
    tr = sim.metrics(sim.run(G["bars"], G["sig"], p, G["warm"], G["split"], G["capital"], c))
    te = sim.metrics(sim.run(G["bars"], G["sig"], p, G["split"], G["T"], G["capital"], c))
    return {"params": p, "train": tr, "test": te}


def spy_return(spy_days, spy_close, d0: str, d1: str) -> float:
    idx = [i for i, d in enumerate(spy_days) if d0 <= d <= d1]
    if len(idx) < 2:
        return float("nan")
    return 100 * (spy_close[idx[-1]] / spy_close[idx[0]] - 1)


def fmt(m: dict) -> str:
    return (f"{m['return_pct']:+.1f}% (CAGR {m['cagr_pct']:+.1f}%, DD {m['max_dd_pct']:.1f}%, "
            f"Sharpe {m['sharpe']:.2f}, {m['trades']} op., {m['win_rate_pct']:.0f}% ganadoras, "
            f"R medio {m['avg_r']:+.2f})")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--bars", default="backtest/cache/bars.npz")
    ap.add_argument("--split", default="2024-07-01")
    ap.add_argument("--capital", type=float, default=111.0)   # 100 € a 1,11
    ap.add_argument("--risk-pct", type=float, default=1.0)
    ap.add_argument("--min-trades", type=int, default=40)
    ap.add_argument("--out", default="backtest/results")
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 2)
    a = ap.parse_args(argv)

    t0 = time.time()
    z = np.load(a.bars, allow_pickle=False)
    bars = {k: z[k] for k in ("open", "high", "low", "close", "volume")}
    days = [str(d) for d in z["days"]]
    sig = signals.compute(bars["close"], bars["high"], bars["low"], bars["volume"])
    split = next(i for i, d in enumerate(days) if d >= a.split)
    G.update(bars=bars, sig=sig, warm=60, split=split, T=len(days), capital=a.capital)
    print(f"datos: {len(days)} días ({days[0]}..{days[-1]}) x {bars['close'].shape[1]} valores; "
          f"señales en {time.time() - t0:.0f}s", flush=True)

    params = grid(a.risk_pct)
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        results = list(ex.map(evaluate, params, chunksize=8))
    print(f"{len(results)} combinaciones en {time.time() - t0:.0f}s", flush=True)

    ok = [r for r in results if r["train"].get("trades", 0) >= a.min_trades]
    ok.sort(key=lambda r: -r["train"]["sharpe"])
    best = ok[0] if ok else None
    cfg = json.loads(Path("config.json").read_text(encoding="utf-8"))  # las reglas que usa el bot hoy
    baseline = evaluate(sim.Params(setups=tuple(cfg["setups"]), stop_atr_mult=cfg["stop_atr_mult"],
                                   target_r=cfg["target_r"], max_hold_days=cfg["max_hold_days"],
                                   max_positions=cfg["max_positions"], max_price_usd=cfg["max_price_usd"],
                                   min_price_usd=cfg["min_price_usd"], risk_pct=a.risk_pct))

    spy_d = [str(d) for d in z["spy_days"]]
    spy_c = z["spy_close"]
    train_span = (days[G["warm"]], days[split - 1])
    test_span = (days[split], days[-1])
    spy_tr = spy_return(spy_d, spy_c, *train_span)
    spy_te = spy_return(spy_d, spy_c, *test_span)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "all_results.csv").open("w", newline="") as f:
        w = csv.writer(f)
        keys = ["return_pct", "cagr_pct", "max_dd_pct", "sharpe", "trades", "win_rate_pct", "avg_r", "profit_factor"]
        w.writerow(["params"] + [f"train_{k}" for k in keys] + [f"test_{k}" for k in keys])
        for r in sorted(results, key=lambda r: -r["train"].get("sharpe", -9)):
            w.writerow([r["params"].label()] + [round(r["train"].get(k, 0), 3) for k in keys]
                       + [round(r["test"].get(k, 0), 3) for k in keys])

    lines = [
        "# Resultado del backtest",
        "",
        f"- Datos: {days[0]} → {days[-1]}, {bars['close'].shape[1]} acciones de EE. UU. con algo de liquidez.",
        f"- Capital: {a.capital:.0f} $ (100 €), riesgo {a.risk_pct:g} % del capital por operación, solo acciones enteras.",
        f"- **Entrenamiento** {train_span[0]} → {train_span[1]} · **Validación** {test_span[0]} → {test_span[1]}.",
        f"- Referencia S&P 500 (SPY, comprar y mantener): entrenamiento {spy_tr:+.1f} %, validación {spy_te:+.1f} %.",
        f"- Combinaciones probadas: {len(results)}; con ≥ {a.min_trades} operaciones en entrenamiento: {len(ok)}.",
        "",
        f"## Reglas actuales del bot (config.json: {baseline['params'].label()}), riesgo {a.risk_pct:g} %",
        f"- Entrenamiento: {fmt(baseline['train'])}",
        f"- Validación: {fmt(baseline['test'])}",
        "",
    ]
    if best:
        lines += [
            "## Mejor combinación (elegida solo con el entrenamiento)",
            f"**{best['params'].label()}**",
            f"- Entrenamiento: {fmt(best['train'])}",
            f"- **Validación (lo que importa): {fmt(best['test'])}**",
            "",
            "## Top 15 en entrenamiento y cómo les fue en validación",
            "| Reglas | Entren. % | Entren. Sharpe | Valid. % | Valid. Sharpe | Valid. DD % | Valid. op. |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in ok[:15]:
            tr, te = r["train"], r["test"]
            lines.append(f"| {r['params'].label()} | {tr['return_pct']:+.1f} | {tr['sharpe']:.2f} | "
                         f"{te['return_pct']:+.1f} | {te['sharpe']:.2f} | {te['max_dd_pct']:.1f} | {te['trades']} |")
        pos_test = sum(1 for r in ok[:15] if r["test"]["return_pct"] > 0)
        lines += ["", f"De las 15 mejores en entrenamiento, **{pos_test} ganan dinero en validación**."]
    lines += ["", "Avisos: universo con sesgo de supervivencia (solo acciones que cotizan hoy); sin calendario de "
              "resultados histórico; deslizamiento 0,01 $/acción. Rentabilidades pasadas no garantizan futuras."]
    report = "\n".join(lines)
    (out / "report.md").write_text(report, encoding="utf-8")
    if best:
        p = best["params"]
        (out / "best_params.json").write_text(json.dumps({
            "setups": list(p.setups), "stop_atr_mult": p.stop_atr_mult, "target_r": p.target_r,
            "max_hold_days": p.max_hold_days, "max_positions": p.max_positions, "max_price_usd": p.max_price_usd,
            "risk_pct": p.risk_pct, "train": best["train"], "test": best["test"]}, indent=2), encoding="utf-8")
    print(report, flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(report + "\n")


if __name__ == "__main__":
    main()
