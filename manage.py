"""GHOST ID admin commands (run on the server).

  python manage.py init              create the owner record and band ID
  python manage.py pair [label]      one-time link that pairs your phone (valid 10 min)
  python manage.py devices           list paired devices
  python manage.py unpair-all        unpair every device (lost or stolen phone)
  python manage.py rotate-band       issue a new band ID (lost or cloned band)
  python manage.py wipe-recon        delete captured visitor recon (photos, GPS, fingerprints)
  python manage.py url               print the URL to write on the band
  python manage.py taps              show the last 20 taps
"""
import sys
import time

from app import db, security
from app.config import settings


def band_url(band_id: str) -> str:
    return f"{settings.base_url}/t/{band_id}"


def fmt(ts) -> str:
    return time.strftime("%d %b %H:%M", time.localtime(ts)) if ts else "-"


def main() -> None:
    db.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    with db.tx() as c:
        owner = db.get_owner(c)
        if cmd == "init":
            if owner:
                sys.exit("Already initialised. Use 'pair' or 'rotate-band'.")
            name = input("Your name: ").strip() or "GHOST"
            band_id = security.new_band_id()
            c.execute("INSERT INTO owner (id, name, band_id) VALUES (1,?,?)", (name, band_id))
            db.audit(c, "owner", "init", name)
            print(f"\nDone. Write this URL to your band:\n\n  {band_url(band_id)}\n")
            print("Next: run 'python manage.py pair' and open the link on your iPhone.")
        elif not owner:
            sys.exit("Run 'python manage.py init' first.")
        elif cmd == "pair":
            label = (" ".join(sys.argv[2:]).strip() or "iPhone")[:40]
            code, now = security.new_token(), time.time()
            c.execute("INSERT INTO pairings (code_hash, label, created, expires) VALUES (?,?,?,?)",
                      (security.token_hash(code), label, now, now + settings.pair_minutes * 60))
            db.audit(c, "owner", "pairing_link_created", label)
            print(f"\nOpen this on your phone within {settings.pair_minutes} minutes (works once):\n\n"
                  f"  {settings.base_url}/pair/{code}\n\nDon't share it: whoever opens it gets full control.")
        elif cmd == "devices":
            rows = c.execute("SELECT * FROM devices WHERE revoked = 0 AND expires > ?", (time.time(),)).fetchall()
            for d in rows:
                print(f"{d['label']:<20} paired {fmt(d['created'])}  last seen {fmt(d['last_seen'])}  {d['ip']}")
            if not rows:
                print("No paired devices. Run 'python manage.py pair'.")
        elif cmd == "unpair-all":
            n = c.execute("UPDATE devices SET revoked = 1 WHERE revoked = 0").rowcount
            c.execute("UPDATE pairings SET used = 1 WHERE used = 0")
            db.audit(c, "owner", "unpair_all", f"{n} devices")
            print(f"Unpaired {n} device(s). Run 'pair' to add your phone again.")
        elif cmd == "wipe-recon":
            n = c.execute("DELETE FROM recon").rowcount
            db.audit(c, "owner", "recon_wiped", f"{n} records")
            print(f"Deleted {n} visitor recon record(s) (photos, locations, fingerprints).")
        elif cmd == "rotate-band":
            band_id = security.new_band_id()
            c.execute("UPDATE owner SET band_id = ? WHERE id = 1", (band_id,))
            db.audit(c, "owner", "band_rotated")
            print(f"Old band URL is dead. Rewrite your band with:\n\n  {band_url(band_id)}\n")
        elif cmd == "url":
            print(band_url(owner["band_id"]))
        elif cmd == "taps":
            for t in c.execute("SELECT * FROM taps ORDER BY id DESC LIMIT 20"):
                who = "OWNER" if t["is_owner"] else "visitor"
                print(fmt(t["ts"]), who, t["ip"], (t["ua"] or "")[:60])
        else:
            print(__doc__)


if __name__ == "__main__":
    main()
