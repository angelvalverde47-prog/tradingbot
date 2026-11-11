"""Configuración: reglas en config.json (en el repositorio) y secretos en variables de entorno de Vercel.

Las claves de Alpaca NUNCA van en config.json ni en el repositorio:
solo en Vercel > Settings > Environment Variables.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.json"


@dataclass
class Config:
    # --- Dinero (en euros; se convierten a dólares con eur_usd) ---
    budget_eur: float = 100.0            # dinero "virtual" que el bot gestiona dentro de la cuenta paper
    cash_buffer_pct: float = 10.0        # % del presupuesto que nunca se invierte
    max_loss_per_trade_eur: float = 2.0  # pérdida máxima si salta el stop
    min_order_eur: float = 5.0
    max_drawdown_eur: float = 15.0       # si las pérdidas cerradas llegan aquí, deja de abrir operaciones
    eur_usd: float = 1.11

    # --- Reglas de la estrategia (swing en gráfico diario) ---
    max_positions: int = 2
    setups: list = field(default_factory=lambda: ["breakout_20d", "bull_flag", "reclaim_ma20"])
    min_price_usd: float = 5.0
    max_price_usd: float = 45.0
    min_dollar_volume_usd: float = 10_000_000   # media de 20 días de precio x volumen
    max_one_day_jump_pct: float = 50.0          # descarta subidas raras (OPA, chicharros)
    stop_atr_mult: float = 1.5
    target_r: float = 2.0
    min_reward_risk: float = 1.5
    earnings_lookahead_days: int = 15           # días naturales sin resultados para abrir
    exit_before_earnings_days: int = 2          # cierra si hay resultados en <= N días naturales
    max_hold_days: int = 15                     # días de mercado; después cierra (stop por tiempo)
    entry_ttl_days: int = 2                     # días de mercado que espera una compra limitada sin ejecutar

    # --- Funcionamiento ---
    start_date: str = "2026-11-11"     # el bot solo mira órdenes suyas desde esta fecha
    data_feed: str = "sip"             # sip (gratis si los datos tienen >15 min) o iex
    paper: bool = True                 # el bot se niega a funcionar si esto es false
    client_id_prefix: str = "swb"

    @property
    def budget_usd(self) -> float:
        return self.budget_eur * self.eur_usd

    @property
    def investable_usd(self) -> float:
        return self.budget_usd * (1 - self.cash_buffer_pct / 100)

    @property
    def max_loss_usd(self) -> float:
        return self.max_loss_per_trade_eur * self.eur_usd

    @property
    def min_order_usd(self) -> float:
        return self.min_order_eur * self.eur_usd

    @property
    def max_drawdown_usd(self) -> float:
        return self.max_drawdown_eur * self.eur_usd

    def eur(self, usd: float) -> float:
        return usd / self.eur_usd


def load_config(path: Path = CONFIG_PATH) -> Config:
    cfg = Config()
    if path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        known = set(asdict(cfg))
        unknown = [k for k in raw if k not in known and not k.startswith("_")]
        if unknown:
            raise ValueError(f"config.json tiene claves desconocidas: {', '.join(unknown)}")
        for k, v in raw.items():
            if not k.startswith("_"):
                setattr(cfg, k, v)
    if not cfg.paper:
        raise ValueError("Este bot solo está preparado para la cuenta PAPER. Pon \"paper\": true en config.json.")
    return cfg


def kill_switch() -> bool:
    """SWINGBOT_KILL=1 en Vercel: no abre operaciones nuevas (las abiertas se siguen gestionando)."""
    return os.environ.get("SWINGBOT_KILL", "").strip().lower() in ("1", "true", "si", "sí", "yes")
