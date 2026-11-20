"""GHOST ID admin commands.

  python manage.py init           create the owner account and band ID
  python manage.py password       change the owner password
  python manage.py rotate-band    issue a new band ID (lost or cloned band)
  python manage.py url            print the URL to write on the band
  python manage.py taps           show the last 20 taps
"""
import getpass
import sys
import time

from app import db, security
from app.config import settings


def ask_password() -> str:
    while True:
        pw = getpass.getpass("Owner password (min 10 chars): ")
        if len(pw) < 10:
            print("Too short.")
            continue
        if pw != getpass.getpass("Repeat password: "):
            print("Passwords don't match.")
            continue
        return pw


def band_url(band_id: str) -> str:
    return f"{settings.base_url}/t/{band_id}"


def main() -> None:
    db.init()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    with db.tx() as c:
        owner = db.get_owner(c)
        if cmd == "init":
            if owner:
                sys.exit("Already initialised. Use 'password' or 'rotate-band'.")
            name = input("Your name: ").strip() or "GHOST"
            band_id = security.new_band_id()
            c.execute("INSERT INTO owner (id, name, pw_hash, band_id) VALUES (1,?,?,?)",
                      (name, security.hash_password(ask_password()), band_id))
            db.audit(c, "owner", "init", name)
            print(f"\nDone. Write this URL to your band:\n\n  {band_url(band_id)}\n")
        elif not owner:
            sys.exit("Run 'python manage.py init' first.")
        elif cmd == "password":
            c.execute("UPDATE owner SET pw_hash = ? WHERE id = 1", (security.hash_password(ask_password()),))
            c.execute("UPDATE sessions SET revoked = 1 WHERE kind = 'owner'")
            db.audit(c, "owner", "password_changed")
            print("Password changed. All owner sessions were logged out.")
        elif cmd == "rotate-band":
            band_id = security.new_band_id()
            c.execute("UPDATE owner SET band_id = ? WHERE id = 1", (band_id,))
            c.execute("UPDATE sessions SET revoked = 1 WHERE kind = 'guest'")
            db.audit(c, "owner", "band_rotated")
            print(f"Old band URL is dead. Rewrite your band with:\n\n  {band_url(band_id)}\n")
        elif cmd == "url":
            print(band_url(owner["band_id"]))
        elif cmd == "taps":
            for t in c.execute("SELECT * FROM taps ORDER BY id DESC LIMIT 20"):
                who = "OWNER" if t["is_owner"] else "visitor"
                print(time.strftime("%d %b %H:%M", time.localtime(t["ts"])), who, t["ip"], t["ua"][:60])
        else:
            print(__doc__)


if __name__ == "__main__":
    main()
