"""Informe de operaciones: /api/report?key=REPORT_KEY (resumen) o &format=csv (para Excel)."""
import html
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swingbot.web import authorized, reply  # noqa: E402


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not authorized(self, "REPORT_KEY"):
            return reply(self, 401, "No autorizado")
        from swingbot.broker import AlpacaBroker
        from swingbot.config import load_config
        from swingbot.core import Bot, eur
        from swingbot.state import CSV_COLUMNS, stats, to_csv
        try:
            cfg = load_config()
            trades = Bot(cfg, AlpacaBroker(cfg.data_feed)).trades()
        except Exception as e:  # noqa: BLE001
            return reply(self, 500, f"ERROR: {e}")
        if parse_qs(urlparse(self.path).query).get("format", [""])[0] == "csv":
            return reply(self, 200, to_csv(trades), "text/csv; charset=utf-8")
        s = stats(trades)
        rows = "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(getattr(t, c) if getattr(t, c) is not None else ''))}</td>"
                             for c in CSV_COLUMNS) + "</tr>" for t in reversed(trades))
        head = "".join(f"<th>{c}</th>" for c in CSV_COLUMNS)
        page = f"""<!doctype html><html lang="es"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Swing bot</title>
<style>body{{font-family:system-ui;margin:16px}}table{{border-collapse:collapse;font-size:13px}}
td,th{{border:1px solid #ccc;padding:4px 6px;white-space:nowrap}}.wrap{{overflow-x:auto}}</style>
<h1>Swing bot (paper)</h1>
<p>{s['closed']} cerradas · {s['win_rate'] if s['win_rate'] is not None else '–'}% ganadoras ·
R medio {s['avg_r'] if s['avg_r'] is not None else '–'} · resultado {eur(cfg, s['pnl_usd'])} ·
{s['open']} abiertas · {s['pending']} esperando</p>
<div class="wrap"><table><tr>{head}</tr>{rows}</table></div></html>"""
        return reply(self, 200, page, "text/html; charset=utf-8")
