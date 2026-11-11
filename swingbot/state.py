"""El bot no tiene disco en Vercel: reconstruye sus operaciones cada día a partir del
historial de órdenes de Alpaca. Cada orden del bot lleva un client_order_id con esta forma:

  entrada:  swb-20261112-RIOT-breakout_20d        (orden bracket: compra + stop + objetivo)
  salida:   swbx-20261112-RIOT-earnings           (venta a mercado del bot: earnings | time)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .broker import val

OPEN_STATES = {"new", "accepted", "held", "pending_new", "partially_filled", "accepted_for_bidding",
               "pending_replace", "replaced", "calculated", "done_for_day"}


@dataclass
class Trade:
    cid: str
    order_id: str
    symbol: str
    setup: str
    entry_day: str
    qty: int
    entry_limit: float
    stop: float | None
    target: float | None
    created_at: datetime | None
    status: str = "pending"          # pending | open | closed | canceled
    entry_price: float | None = None
    opened_at: datetime | None = None
    exit_price: float | None = None
    exit_reason: str | None = None   # take_profit | stop_loss | earnings | time
    closed_at: datetime | None = None
    exiting: bool = False            # el bot ya ha mandado la venta y está pendiente
    legs: list = field(default_factory=list)

    @property
    def pnl_usd(self) -> float | None:
        if self.exit_price is None or self.entry_price is None:
            return None
        return round((self.exit_price - self.entry_price) * self.qty, 2)

    @property
    def r_multiple(self) -> float | None:
        if self.pnl_usd is None or not self.stop:
            return None
        risk = (self.entry_price - self.stop) * self.qty
        return round(self.pnl_usd / risk, 2) if risk > 0 else None


def entry_cid(prefix: str, day: str, symbol: str, setup: str) -> str:
    return f"{prefix}-{day}-{symbol}-{setup}"


def exit_cid(prefix: str, day: str, symbol: str, reason: str) -> str:
    return f"{prefix}x-{day}-{symbol}-{reason}"


def reconstruct(orders: list, prefix: str) -> list[Trade]:
    entries, exits = [], {}
    for o in orders:
        cid = o.client_order_id or ""
        if cid.startswith(prefix + "x-"):
            _, day, sym, reason = cid.split("-", 3)
            exits[(day, sym)] = (o, reason)
        elif cid.startswith(prefix + "-"):
            entries.append(o)
    trades = []
    for o in entries:
        _, day, sym, setup = o.client_order_id.split("-", 3)
        legs = list(o.legs or [])
        tp = next((leg for leg in legs if val(leg.type) == "limit"), None)
        sl = next((leg for leg in legs if val(leg.type) in ("stop", "stop_limit")), None)
        t = Trade(cid=o.client_order_id, order_id=str(o.id), symbol=sym, setup=setup, entry_day=day,
                  qty=int(float(o.qty or 0)), entry_limit=float(o.limit_price or 0),
                  stop=float(sl.stop_price) if sl and sl.stop_price else None,
                  target=float(tp.limit_price) if tp and tp.limit_price else None,
                  created_at=o.submitted_at or o.created_at, legs=legs)
        filled = float(o.filled_qty or 0)
        st = val(o.status)
        if filled <= 0:
            t.status = "pending" if st in OPEN_STATES else "canceled"
            trades.append(t)
            continue
        t.qty = int(filled)
        t.entry_price = float(o.filled_avg_price)
        t.opened_at = o.filled_at
        t.status = "open"
        for leg, reason in ((tp, "take_profit"), (sl, "stop_loss")):
            if leg is not None and val(leg.status) == "filled":
                t.status, t.exit_reason = "closed", reason
                t.exit_price, t.closed_at = float(leg.filled_avg_price), leg.filled_at
        if t.status != "closed" and (day, sym) in exits:
            xo, reason = exits[(day, sym)]
            if val(xo.status) == "filled":
                t.status, t.exit_reason = "closed", reason
                t.exit_price, t.closed_at = float(xo.filled_avg_price), xo.filled_at
            elif val(xo.status) in OPEN_STATES:
                t.exiting, t.exit_reason = True, reason
        trades.append(t)
    trades.sort(key=lambda t: (t.entry_day, t.symbol))
    return trades


def stats(trades: list[Trade]) -> dict:
    closed = [t for t in trades if t.status == "closed"]
    wins = [t for t in closed if (t.pnl_usd or 0) > 0]
    rs = [t.r_multiple for t in closed if t.r_multiple is not None]
    return {
        "closed": len(closed),
        "wins": len(wins),
        "win_rate": round(100 * len(wins) / len(closed), 1) if closed else None,
        "avg_r": round(sum(rs) / len(rs), 2) if rs else None,
        "pnl_usd": round(sum(t.pnl_usd or 0 for t in closed), 2),
        "open": len([t for t in trades if t.status == "open"]),
        "pending": len([t for t in trades if t.status == "pending"]),
    }


CSV_COLUMNS = ["entry_day", "symbol", "setup", "status", "qty", "entry_limit", "entry_price", "stop",
               "target", "opened_at", "exit_price", "exit_reason", "closed_at", "pnl_usd", "r_multiple"]


def to_csv(trades: list[Trade]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for t in trades:
        w.writerow([getattr(t, c) if getattr(t, c) is not None else "" for c in CSV_COLUMNS])
    return buf.getvalue()
