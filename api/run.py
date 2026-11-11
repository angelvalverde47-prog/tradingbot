"""Pasada diaria. La lanza el cron de Vercel (vercel.json) o tú a mano con /api/run?key=CRON_SECRET."""
import logging
import traceback
from http.server import BaseHTTPRequestHandler

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from swingbot.web import authorized, reply  # noqa: E402

logging.basicConfig(level=logging.INFO)
VERSION = "2026-11-11b (calendario Finnhub/Nasdaq en paralelo)"


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if not authorized(self, "CRON_SECRET"):
            return reply(self, 401, "No autorizado")
        from swingbot import notify
        from swingbot.broker import AlpacaBroker
        from swingbot.config import load_config
        from swingbot.core import Bot
        logging.info("swingbot versión %s", VERSION)
        try:
            cfg = load_config()
            text = Bot(cfg, AlpacaBroker(cfg.data_feed)).run_day()
            return reply(self, 200, f"{text}\n\n(versión {VERSION})")
        except Exception as e:  # noqa: BLE001
            logging.exception("fallo en la pasada diaria")
            notify.send("Swing bot: ERROR", f"La pasada de hoy ha fallado: {e}")
            return reply(self, 500, f"ERROR: {e}\n\n{traceback.format_exc()}")
