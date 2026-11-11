import hmac
import os
from urllib.parse import parse_qs, urlparse



def authorized(handler, env_name: str) -> bool:
    """Vale la cabecera que manda el cron de Vercel (Authorization: Bearer CRON_SECRET)
    o ?key=... en la URL, para lanzarlo a mano desde el navegador."""
    secret = os.environ.get(env_name, "")
    if not secret:
        return False
    header = handler.headers.get("Authorization", "")
    key = parse_qs(urlparse(handler.path).query).get("key", [""])[0]
    try:
        return hmac.compare_digest(header, f"Bearer {secret}") or hmac.compare_digest(key, secret)
    except TypeError:  # caracteres no ASCII
        return False


def reply(handler, status: int, body: str, ctype: str = "text/plain; charset=utf-8"):
    data = body.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(data)
