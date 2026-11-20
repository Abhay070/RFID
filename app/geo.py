"""Best-effort coarse location from an IP address.

Runs in a background thread so it never slows a tap, and fails silently so a
blocked network or offline host simply leaves the location blank. Set
GHOST_GEO=0 to turn it off entirely.
"""
import ipaddress
import json
import threading
import urllib.request

from . import db
from .config import settings


def _private(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_private
    except ValueError:
        return True  # "?", hostnames, anything unparseable -> skip


def _lookup(tap_id: int, ip: str) -> None:
    try:
        req = urllib.request.Request(
            f"https://ipapi.co/{ip}/json/",
            headers={"User-Agent": "ghost-id"},
        )
        with urllib.request.urlopen(req, timeout=3) as r:
            d = json.loads(r.read().decode("utf-8", "ignore"))
        geo = {
            "city": d.get("city"),
            "region": d.get("region"),
            "country": d.get("country_name"),
            "org": d.get("org"),
        }
        if not any(geo.values()):
            return
        with db.tx() as c:
            c.execute("UPDATE taps SET geo = ? WHERE id = ?", (json.dumps(geo), tap_id))
    except Exception as exc:  # never let a lookup break anything
        print(f"[ghost-id] geo lookup failed: {exc}")


def lookup_async(tap_id: int, ip: str) -> None:
    if not settings.geo_enabled or _private(ip):
        return
    threading.Thread(target=_lookup, args=(tap_id, ip), daemon=True).start()
