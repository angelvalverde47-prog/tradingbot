"""Todo lo que habla con Alpaca está aquí. Para pasar a IBKR en el futuro,
se escribe otra clase con los mismos métodos y el resto del bot no cambia."""
from __future__ import annotations

import logging
import os
import time
from datetime import date, datetime, timedelta, timezone

from alpaca.common.enums import Sort
from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (AssetClass, AssetStatus, OrderClass, OrderSide, QueryOrderStatus,
                                  TimeInForce)
from alpaca.trading.requests import (GetAssetsRequest, GetCalendarRequest, GetOrdersRequest,
                                     LimitOrderRequest, MarketOrderRequest, StopLossRequest,
                                     TakeProfitRequest)

from .strategy import Bars, Ticket

log = logging.getLogger("swingbot.broker")

EXCHANGES = {"NYSE", "NASDAQ", "AMEX", "ARCA", "BATS"}
FUND_WORDS = ("ETF", "ETN", " FUND", "ISHARES", "PROSHARES", "DIREXION", "LEVERAGED", " 2X", " 3X",
              "ULTRA", "WARRANT", "RIGHT", " UNIT", "PREFERRED", "DEPOSITARY SHARES REP")


def val(x) -> str:
    """Enum de alpaca-py o texto -> texto."""
    return getattr(x, "value", str(x)) if x is not None else ""


class AlpacaBroker:
    def __init__(self, data_feed: str = "sip"):
        key, secret = os.environ.get("ALPACA_API_KEY"), os.environ.get("ALPACA_SECRET_KEY")
        if not key or not secret:
            raise RuntimeError("Faltan ALPACA_API_KEY y ALPACA_SECRET_KEY en las variables de entorno de Vercel.")
        # paper=True fijo: este bot no puede hablar con una cuenta real.
        self.trading = TradingClient(key, secret, paper=True)
        self.data = StockHistoricalDataClient(key, secret)
        self.feed = DataFeed(data_feed)

    # ---------- mercado ----------
    def trading_days(self, start: date, end: date) -> list[date]:
        if end < start:
            return []
        return [c.date for c in self.trading.get_calendar(GetCalendarRequest(start=start, end=end))]

    def is_trading_day(self, d: date) -> bool:
        return d in self.trading_days(d, d)

    def market_days_between(self, start: date, end: date) -> int:
        """Días de mercado en (start, end]."""
        return len(self.trading_days(start + timedelta(days=1), end)) if end > start else 0

    def previous_trading_day(self, d: date) -> date:
        days = self.trading_days(d - timedelta(days=10), d - timedelta(days=1))
        return days[-1] if days else d - timedelta(days=1)

    # ---------- universo y datos ----------
    def universe(self) -> list[str]:
        assets = self.trading.get_all_assets(GetAssetsRequest(status=AssetStatus.ACTIVE,
                                                              asset_class=AssetClass.US_EQUITY))
        out = []
        for a in assets:
            name = (a.name or "").upper()
            if not a.tradable or val(a.exchange) not in EXCHANGES:
                continue
            if not a.symbol.isalpha() or len(a.symbol) > 5:
                continue
            if any(w in name for w in FUND_WORDS):
                continue
            out.append(a.symbol)
        return sorted(out)

    def daily_bars(self, symbols: list[str], days: int = 90, batch: int = 400) -> dict[str, Bars]:
        end = datetime.now(timezone.utc) - timedelta(minutes=20)  # el SIP gratuito exige >15 min de antigüedad
        start = end - timedelta(days=days)
        out: dict[str, Bars] = {}
        for i in range(0, len(symbols), batch):
            chunk = symbols[i:i + batch]
            try:
                res = self.data.get_stock_bars(StockBarsRequest(
                    symbol_or_symbols=chunk, timeframe=TimeFrame.Day, start=start, end=end,
                    adjustment=Adjustment.ALL, feed=self.feed))
            except Exception as e:  # noqa: BLE001
                log.warning("velas %s..%s: %s", chunk[0], chunk[-1], e)
                if "subscription" in str(e).lower():
                    raise
                continue
            for sym, bars in res.data.items():
                if bars:
                    out[sym] = Bars(sym, [b.high for b in bars], [b.low for b in bars],
                                    [b.close for b in bars], [b.volume for b in bars])
        return out

    # ---------- órdenes ----------
    def bot_orders(self, prefix: str, since: date) -> list:
        """Todas las órdenes del bot (por prefijo de client_order_id) desde `since`, con sus patas."""
        out, until = [], None
        after = datetime.combine(since, datetime.min.time(), tzinfo=timezone.utc)
        while True:
            page = self.trading.get_orders(GetOrdersRequest(
                status=QueryOrderStatus.ALL, limit=500, after=after, until=until, nested=True,
                direction=Sort.DESC))
            out += [o for o in page if (o.client_order_id or "").startswith(prefix)]
            if len(page) < 500:
                return out
            until = min(o.submitted_at or o.created_at for o in page)

    def place_bracket(self, t: Ticket, client_order_id: str) -> str:
        o = self.trading.submit_order(LimitOrderRequest(
            symbol=t.symbol, qty=t.qty, side=OrderSide.BUY, time_in_force=TimeInForce.GTC,
            limit_price=t.entry, order_class=OrderClass.BRACKET, client_order_id=client_order_id,
            take_profit=TakeProfitRequest(limit_price=t.target),
            stop_loss=StopLossRequest(stop_price=t.stop)))
        return str(o.id)

    def cancel(self, order_id: str):
        self.trading.cancel_order_by_id(order_id)

    def has_position(self, symbol: str) -> bool:
        try:
            self.trading.get_open_position(symbol)
            return True
        except Exception:  # noqa: BLE001
            return False

    def exit_position(self, symbol: str, qty: int, legs: list, client_order_id: str) -> str:
        """Cancela el stop y el objetivo del bracket y vende a mercado con un id del bot."""
        for leg in legs:
            if val(leg.status) in ("new", "accepted", "held", "partially_filled", "pending_new"):
                try:
                    self.trading.cancel_order_by_id(leg.id)
                except Exception as e:  # noqa: BLE001
                    log.warning("cancelar %s: %s", leg.id, e)
        last = None
        for _ in range(8):  # la cancelación tarda unos segundos en liberar las acciones
            try:
                o = self.trading.submit_order(MarketOrderRequest(
                    symbol=symbol, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
                    client_order_id=client_order_id))
                return str(o.id)
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(2)
        raise RuntimeError(f"no se pudo vender {symbol}: {last}")
