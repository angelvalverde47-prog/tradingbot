"""La pasada diaria del bot. La llama api/run.py (cron de Vercel) una vez al día."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from . import earnings, notify
from .config import Config, kill_switch
from .state import Trade, entry_cid, exit_cid, reconstruct, stats
from .strategy import make_ticket, rank

ET = ZoneInfo("America/New_York")
log = logging.getLogger("swingbot")
REASONS = {"take_profit": "objetivo", "stop_loss": "stop", "earnings": "resultados", "time": "tiempo"}


def eur(cfg: Config, usd: float) -> str:
    return f"{cfg.eur(usd):+.2f} €".replace(".", ",")


def eur_abs(cfg: Config, usd: float) -> str:
    return f"{cfg.eur(usd):.2f} €".replace(".", ",")


def et_date(dt: datetime) -> date:
    return dt.astimezone(ET).date()


class Bot:
    def __init__(self, cfg: Config, broker, earnings_fn=earnings.upcoming, kill_fn=kill_switch):
        self.cfg, self.broker, self.earnings_fn, self.kill_fn = cfg, broker, earnings_fn, kill_fn
        self.warnings: list[str] = []

    def trades(self) -> list[Trade]:
        start = date.fromisoformat(self.cfg.start_date)
        return reconstruct(self.broker.bot_orders(self.cfg.client_id_prefix, start), self.cfg.client_id_prefix)

    # ---------- gestionar abiertas y pendientes ----------
    def manage(self, trades: list[Trade], today: date, earn: dict | None):
        p = self.cfg.client_id_prefix
        for t in trades:
            if t.status == "open" and not t.exiting:
                if not self.broker.has_position(t.symbol):
                    self.warnings.append(f"{t.symbol}: no hay posición en Alpaca y no sé cómo se cerró (¿a mano?).")
                    continue
                days = self.broker.market_days_between(et_date(t.opened_at), today)
                reason = None
                if earn is not None and t.symbol in earn and \
                        (earn[t.symbol] - today).days <= self.cfg.exit_before_earnings_days:
                    reason = "earnings"
                elif days >= self.cfg.max_hold_days:
                    reason = "time"
                if reason:
                    try:
                        self.broker.exit_position(t.symbol, t.qty, t.legs, exit_cid(p, t.entry_day, t.symbol, reason))
                        t.exiting, t.exit_reason = True, reason
                        self.warnings.append(f"{t.symbol}: venta a mercado enviada ("
                                             f"{'resultados próximos' if reason == 'earnings' else f'{days} días abierta'}).")
                    except Exception as e:  # noqa: BLE001
                        self.warnings.append(f"{t.symbol}: no se pudo cerrar: {e}")
            elif t.status == "pending":
                age = self.broker.market_days_between(et_date(t.created_at), today)
                if age >= self.cfg.entry_ttl_days:
                    try:
                        self.broker.cancel(t.order_id)
                        t.status = "canceled"
                        self.warnings.append(f"{t.symbol}: compra limitada sin ejecutar en {age} días, cancelada.")
                    except Exception as e:  # noqa: BLE001
                        self.warnings.append(f"{t.symbol}: no se pudo cancelar la compra: {e}")

    # ---------- operaciones nuevas ----------
    def new_entries(self, trades: list[Trade], today: date, earn: dict | None) -> list[str]:
        if self.kill_fn():
            self.warnings.append("Interruptor SWINGBOT_KILL activo: no se abren operaciones nuevas.")
            return []
        if -stats(trades)["pnl_usd"] >= self.cfg.max_drawdown_usd:
            self.warnings.append(f"Pérdidas acumuladas ≥ {eur_abs(self.cfg, self.cfg.max_drawdown_usd)}: "
                                 "el bot ha dejado de abrir operaciones. Revisa la estrategia.")
            return []
        if earn is None:
            self.warnings.append("No se pudo leer el calendario de resultados: hoy no se abren operaciones.")
            return []
        active = [t for t in trades if t.status in ("open", "pending")]
        busy = {t.symbol for t in active}
        slots = self.cfg.max_positions - len(active)
        if slots <= 0:
            return []
        cash = self.cfg.investable_usd - sum(t.qty * (t.entry_price or t.entry_limit) for t in active)
        symbols = self.broker.universe()
        bars = self.broker.daily_bars(symbols)
        log.info("universo %d valores, con datos %d", len(symbols), len(bars))
        cands = []
        for sym, b in bars.items():
            if sym in busy or sym in earn:
                continue
            tk, _why = make_ticket(b, self.cfg, cash)
            if tk:
                cands.append(tk)
        lines = []
        for tk in rank(cands):
            if slots <= 0:
                break
            # Volver a leer Alpaca justo antes de cada compra: si otra pasada (otra pestaña, el cron)
            # ha abierto posiciones mientras esta escaneaba, se respetan igualmente los límites.
            fresh = [t for t in self.trades() if t.status in ("open", "pending")]
            if len(fresh) >= self.cfg.max_positions:
                self.warnings.append("Otra pasada ya ha ocupado los huecos libres; esta no abre nada más.")
                break
            if tk.symbol in {t.symbol for t in fresh}:
                continue
            slots = self.cfg.max_positions - len(fresh)
            cash = min(cash, self.cfg.investable_usd - sum(t.qty * (t.entry_price or t.entry_limit) for t in fresh))
            if tk.cost > cash:  # no llega el efectivo: menos acciones, misma regla de mínimo
                tk.qty = int(cash // tk.entry)
                if tk.qty < 1 or tk.cost < self.cfg.min_order_usd:
                    continue
            cid = entry_cid(self.cfg.client_id_prefix, f"{today:%Y%m%d}", tk.symbol, tk.setup)
            try:
                self.broker.place_bracket(tk, cid)
            except Exception as e:  # noqa: BLE001
                self.warnings.append(f"{tk.symbol}: Alpaca rechazó la orden: {e}")
                continue
            cash -= tk.cost
            slots -= 1
            lines.append(f"{tk.symbol} ({tk.setup}): compra limitada {tk.qty} a {tk.entry:.2f} $, "
                         f"stop {tk.stop:.2f}, objetivo {tk.target:.2f} · coste {eur_abs(self.cfg, tk.cost)}, "
                         f"riesgo máx. {eur_abs(self.cfg, tk.max_loss)}")
        log.info("candidatos %d, órdenes nuevas %d", len(cands), len(lines))
        return lines

    # ---------- pasada diaria ----------
    def run_day(self, today: date | None = None, send: bool = True) -> str:
        today = today or datetime.now(ET).date()
        self.warnings = []
        if not self.broker.is_trading_day(today):
            return f"{today:%d/%m}: hoy no hay mercado en EE. UU. Nada que hacer."
        trades = self.trades()
        prev = self.broker.previous_trading_day(today)
        since = datetime.combine(prev, time(7, 0), tzinfo=ET)
        just_closed = [t for t in trades if t.status == "closed" and t.closed_at and t.closed_at >= since]
        earn = self.earnings_fn(self.cfg.earnings_lookahead_days, today)
        self.manage(trades, today, earn)
        new = self.new_entries(trades, today, earn)
        text = self.summary(trades, just_closed, new)
        if send:
            notify.send(f"Swing bot (paper) {today:%d/%m}", text)
        return text

    def summary(self, trades: list[Trade], just_closed: list[Trade], new: list[str]) -> str:
        c, out = self.cfg, []
        if just_closed:
            out.append("Cerradas: " + "; ".join(
                f"{t.symbol} {eur(c, t.pnl_usd)} ({REASONS.get(t.exit_reason, t.exit_reason)}, {t.r_multiple:+.2f}R)"
                for t in just_closed))
        opens = [t for t in trades if t.status == "open"]
        if opens:
            out.append("Abiertas: " + "; ".join(
                f"{t.symbol} x{t.qty} a {t.entry_price:.2f} (stop {t.stop:.2f}, obj. {t.target:.2f})"
                + (" · vendiendo" if t.exiting else "") for t in opens))
        pend = [t for t in trades if t.status == "pending"]
        if pend:
            out.append("Esperando ejecución: " + "; ".join(f"{t.symbol} x{t.qty} a {t.entry_limit:.2f}" for t in pend))
        out.append("Nuevas: " + ("; ".join(new) if new else "ninguna hoy"))
        s = stats(trades)
        if s["closed"]:
            out.append(f"Acumulado: {s['closed']} cerradas, {s['win_rate']}% ganadoras, "
                       f"R medio {s['avg_r']}, resultado {eur(c, s['pnl_usd'])}")
        out += [f"⚠ {w}" for w in self.warnings]
        return "\n".join(out)
