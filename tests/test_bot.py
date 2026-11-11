"""Pruebas sin red: estrategia, tamaño, límites y ciclo completo con un Alpaca simulado.
El estado se reconstruye siempre desde las órdenes, como en Vercel."""
import math
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swingbot import core  # noqa: E402
from swingbot.config import Config  # noqa: E402
from swingbot.state import stats  # noqa: E402
from swingbot.strategy import Bars, atr, make_ticket, setups_for  # noqa: E402


def breakout_bars(sym="ABC", base=20.0, n=60, last_jump=0.6, rng=0.4, vol=2_000_000):
    closes = [base + 0.1 * math.sin(i) for i in range(n - 1)] + [base + last_jump]
    return Bars(sym, [c + rng / 2 for c in closes], [c - rng / 2 for c in closes], closes, [vol] * n)


def test_breakout_detected_and_ticket_respects_limits():
    cfg = Config()
    b = breakout_bars()
    assert "breakout_20d" in setups_for(b)
    t, why = make_ticket(b, cfg, cfg.investable_usd)
    assert t, why
    assert t.stop < t.entry < t.target
    assert t.max_loss <= cfg.max_loss_usd + 1e-9
    assert t.cost <= cfg.investable_usd
    assert abs(t.reward_risk - cfg.target_r) < 0.05
    assert t.stop == math.floor((t.entry - 1.5 * atr(b)) * 100) / 100


def test_rejections():
    cfg = Config()
    assert make_ticket(breakout_bars(base=80), cfg, 1000)[1].startswith("precio")
    assert make_ticket(breakout_bars(vol=1000), cfg, 1000)[1] == "poca liquidez"
    assert "stop demasiado lejos" in make_ticket(breakout_bars(rng=4), cfg, 1000)[1]
    assert make_ticket(breakout_bars(), cfg, 5)[1] == "no queda efectivo"


def utc(d: date, h=15):
    return datetime(d.year, d.month, d.day, h, 0, tzinfo=timezone.utc)


class FakeAlpaca:
    """Imita lo justo de Alpaca: órdenes bracket con patas, ventas a mercado, posiciones."""

    def __init__(self, bars):
        self.bars, self.orders, self.positions, self.n, self.now = bars, [], {}, 0, utc(date(2026, 11, 12), 10)

    def _id(self):
        self.n += 1
        return f"id{self.n}"

    def trading_days(self, s, e):
        return [s + timedelta(days=i) for i in range((e - s).days + 1) if (s + timedelta(days=i)).weekday() < 5]

    def is_trading_day(self, d):
        return d.weekday() < 5

    def market_days_between(self, s, e):
        return len(self.trading_days(s + timedelta(days=1), e)) if e > s else 0

    def previous_trading_day(self, d):
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d

    def universe(self):
        return list(self.bars)

    def daily_bars(self, symbols):
        return {s: self.bars[s] for s in symbols}

    def bot_orders(self, prefix, since):
        return [o for o in self.orders if o.client_order_id.startswith(prefix)]

    def place_bracket(self, t, cid):
        assert all(o.client_order_id != cid for o in self.orders), "client_order_id repetido"
        legs = [NS(id=self._id(), type="limit", status="held", limit_price=t.target, stop_price=None,
                   filled_avg_price=None, filled_at=None),
                NS(id=self._id(), type="stop", status="held", limit_price=None, stop_price=t.stop,
                   filled_avg_price=None, filled_at=None)]
        o = NS(id=self._id(), client_order_id=cid, symbol=t.symbol, qty=str(t.qty), limit_price=t.entry,
               status="new", filled_qty="0", filled_avg_price=None, filled_at=None, legs=legs,
               submitted_at=self.now, created_at=self.now)
        self.orders.append(o)
        return o.id

    def cancel(self, oid):
        next(o for o in self.orders if o.id == oid).status = "canceled"

    def has_position(self, s):
        return s in self.positions

    def exit_position(self, symbol, qty, legs, cid):
        for leg in legs:
            leg.status = "canceled"
        o = NS(id=self._id(), client_order_id=cid, symbol=symbol, qty=str(qty), status="new",
               filled_qty="0", filled_avg_price=None, filled_at=None, legs=[], submitted_at=self.now,
               created_at=self.now, limit_price=None)
        self.orders.append(o)
        return o.id

    # --- simulación del mercado ---
    def entry(self, sym):
        return next(o for o in self.orders if o.symbol == sym and o.legs)

    def fill_entry(self, sym, d):
        o = self.entry(sym)
        o.status, o.filled_qty, o.filled_avg_price, o.filled_at = "filled", o.qty, o.limit_price, utc(d)
        for leg in o.legs:
            leg.status = "new"
        self.positions[sym] = int(o.qty)

    def hit(self, sym, which, d):
        o = self.entry(sym)
        leg = o.legs[0 if which == "target" else 1]
        leg.status, leg.filled_at = "filled", utc(d)
        leg.filled_avg_price = leg.limit_price if which == "target" else leg.stop_price
        o.legs[1 if which == "target" else 0].status = "canceled"
        self.positions.pop(sym, None)

    def fill_exits(self, d, price):
        for o in self.orders:
            if "x-" in o.client_order_id and o.status == "new":
                o.status, o.filled_avg_price, o.filled_at, o.filled_qty = "filled", price, utc(d), o.qty
                self.positions.pop(o.symbol, None)


def make_bot(bars, earn=None, kill=False):
    fa = FakeAlpaca(bars)
    bot = core.Bot(Config(), fa, earnings_fn=lambda days, today: {} if earn is None else earn,
                   kill_fn=lambda: kill)
    return bot, fa


def run(bot, fa, d):
    fa.now = utc(d, 10)
    return bot.run_day(d, send=False)


def test_full_cycle_target_and_stop():
    bars = {s: breakout_bars(s, base=b, vol=v) for s, b, v in [("AAA", 20, 5e6), ("BBB", 15, 4e6), ("CCC", 12, 3e6)]}
    bot, fa = make_bot(bars)
    d1, d2, d3 = date(2026, 11, 12), date(2026, 11, 13), date(2026, 11, 16)
    text = run(bot, fa, d1)
    assert "AAA" in text and "BBB" in text and "CCC" not in text      # máx. 2, el más negociado primero
    t = bot.trades()
    assert sum(x.qty * x.entry_limit for x in t) <= bot.cfg.investable_usd
    fa.fill_entry("AAA", d1); fa.fill_entry("BBB", d1)
    text = run(bot, fa, d2)
    assert "Abiertas" in text and "Nuevas: ninguna" in text
    fa.hit("AAA", "target", d2); fa.hit("BBB", "stop", d2)
    text = run(bot, fa, d3)
    closed = {x.symbol: x for x in bot.trades() if x.status == "closed"}
    assert closed["AAA"].exit_reason == "take_profit" and closed["AAA"].pnl_usd > 0
    assert closed["BBB"].exit_reason == "stop_loss" and abs(closed["BBB"].r_multiple + 1) < 0.01
    assert text.startswith("Cerradas") and "Acumulado: 2 cerradas, 50.0%" in text
    assert "Nuevas: AAA" in text                                         # huecos libres -> nuevas entradas
    assert len({o.client_order_id for o in fa.orders}) == len(fa.orders)  # ids nunca repetidos


def test_stale_entry_cancelled_then_earnings_exit():
    bot, fa = make_bot({"AAA": breakout_bars("AAA", vol=5e6)})
    run(bot, fa, date(2026, 11, 12))
    text = run(bot, fa, date(2026, 11, 16))                   # 2 días de mercado sin ejecutarse
    assert "cancelada" in text
    assert any(o.status == "canceled" for o in fa.orders)
    assert "AAA" in text.split("Nuevas:")[1]                  # vuelve a entrar con otro id
    fa.fill_entry("AAA", date(2026, 11, 16))
    bot.earnings_fn = lambda days, today: {"AAA": today + timedelta(days=1)}
    text = run(bot, fa, date(2026, 11, 17))
    assert "venta a mercado enviada" in text
    fa.fill_exits(date(2026, 11, 17), 20.9)
    bot.earnings_fn = lambda days, today: {}
    run(bot, fa, date(2026, 11, 18))
    tr = [x for x in bot.trades() if x.status == "closed"][0]
    assert tr.exit_reason == "earnings" and tr.exit_price == 20.9


def test_time_exit():
    bot, fa = make_bot({"AAA": breakout_bars("AAA", vol=5e6)})
    bot.cfg.max_hold_days = 3
    run(bot, fa, date(2026, 11, 12))
    fa.fill_entry("AAA", date(2026, 11, 12))
    assert "venta" not in run(bot, fa, date(2026, 11, 16))
    assert "3 días abierta" in run(bot, fa, date(2026, 11, 17))


def test_guards_kill_calendar_drawdown_weekend():
    bot, fa = make_bot({"AAA": breakout_bars("AAA", vol=5e6)}, kill=True)
    assert "KILL" in run(bot, fa, date(2026, 11, 12)) and not fa.orders
    bot.kill_fn = lambda: False
    bot.earnings_fn = lambda days, today: None
    assert "calendario" in run(bot, fa, date(2026, 11, 12)) and not fa.orders
    assert "no hay mercado" in run(bot, fa, date(2026, 11, 14))
    bot.earnings_fn = lambda days, today: {}
    bot.cfg.max_drawdown_eur = 0.0
    assert "dejado de abrir" in run(bot, fa, date(2026, 11, 12))


def test_stats_from_orders_only():
    bot, fa = make_bot({"AAA": breakout_bars("AAA", vol=5e6)})
    run(bot, fa, date(2026, 11, 12))
    fa.fill_entry("AAA", date(2026, 11, 12)); fa.hit("AAA", "target", date(2026, 11, 13))
    s = stats(bot.trades())
    assert s["closed"] == 1 and s["wins"] == 1 and s["avg_r"] == 2.0
