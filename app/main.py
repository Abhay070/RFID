"""GHOST ID core server.

Design rule: the band identifies the entry point, the owner's paired iPhone is
the key, and this backend enforces every permission.

- A tap from the owner's paired phone opens the Command dashboard.
- A tap from any other phone shows only the public promotional profile.
- Owner pages answer 404 to everyone else, so they reveal nothing.

A tap alone never grants anything: the NTAG213 band can be cloned, so control
depends on a device token that only exists on the owner's paired phone.
"""
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, notify, security
from .config import settings

BASE = Path(__file__).resolve().parent
DEVICE_COOKIE = "ghost_device"

app = FastAPI(title="GHOST ID", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

db.init()

pair_limiter = security.RateLimiter(limit=10, window=15 * 60)


# ---------------------------------------------------------------- helpers
def fmt_time(ts: float | None) -> str:
    return time.strftime("%d %b %H:%M:%S", time.localtime(ts)) if ts else "-"


templates.env.filters["t"] = fmt_time


def client_ip(request: Request) -> str:
    if settings.trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "?"


def load_profile() -> dict:
    path = Path(settings.profile_path)
    if not path.exists():
        path = BASE.parent / "profile.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def owner_device(request: Request):
    """The paired device making this request, or None."""
    token = request.cookies.get(DEVICE_COOKIE)
    if not token:
        return None
    with db.tx() as c:
        row = c.execute("SELECT * FROM devices WHERE token_hash = ?",
                        (security.token_hash(token),)).fetchone()
        if not row or row["revoked"] or row["expires"] < time.time():
            return None
        c.execute("UPDATE devices SET last_seen = ? WHERE id = ?", (time.time(), row["id"]))
    return row


def require_owner(request: Request):
    """Owner-only pages look exactly like missing pages to everyone else."""
    dev = owner_device(request)
    if not dev:
        raise HTTPException(status_code=404)
    return dev


def require_band(band_id: str):
    with db.tx() as c:
        owner = db.get_owner(c)
    if not owner or not same(band_id, owner["band_id"]):
        raise HTTPException(status_code=404)
    return owner


# ---------------------------------------------------------------- middleware
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # Block cross-site form posts (CSRF): the Origin of a POST must be this site.
    if request.method == "POST":
        origin = request.headers.get("origin")
        if origin and origin != "null":
            expected = urlparse(settings.base_url).netloc
            if urlparse(origin).netloc not in (expected, request.url.netloc):
                return JSONResponse({"detail": "cross-site request blocked"}, status_code=403)
    resp = await call_next(request)
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
        "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    )
    if request.url.path.startswith(("/admin", "/pair", "/t/")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    if exc.status_code == 404:
        return templates.TemplateResponse(request, "message.html", {
            "title": "Not found", "message": "There's nothing here."}, status_code=404)
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


# ---------------------------------------------------------------- the tap
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {"profile": load_profile()})


@app.get("/t/{band_id}")
def tap(request: Request, band_id: str):
    require_band(band_id)
    dev = owner_device(request)
    ip, ua = client_ip(request), request.headers.get("user-agent", "")[:200]
    with db.tx() as c:
        c.execute("INSERT INTO taps (ts, band_id, ip, ua, is_owner) VALUES (?,?,?,?,?)",
                  (time.time(), band_id, ip, ua, int(dev is not None)))
        if dev:
            db.audit(c, f"owner:{dev['label']}", "owner_tap", "", ip)
        else:
            db.audit(c, "visitor", "tap", ua[:80], ip)
    if dev:
        return RedirectResponse("/admin", status_code=303)
    if settings.notify_taps:
        notify.push("GHOST ID: band tapped", f"Someone opened your profile ({ip}).", tags="wave")
    return templates.TemplateResponse(request, "profile.html", {
        "profile": load_profile(), "band_id": band_id})


@app.get("/t/{band_id}/contact.vcf")
def vcard(band_id: str):
    require_band(band_id)
    p = load_profile()
    contact = p.get("contact", {})
    lines = ["BEGIN:VCARD", "VERSION:3.0", f"FN:{p.get('name', '')}"]
    if p.get("tagline"):
        lines.append(f"TITLE:{p['tagline']}")
    if contact.get("email"):
        lines.append(f"EMAIL;TYPE=INTERNET:{contact['email']}")
    if contact.get("phone"):
        lines.append(f"TEL;TYPE=CELL:{contact['phone']}")
    for link in p.get("links", []):
        lines.append(f"URL:{link['url']}")
    lines.append("END:VCARD")
    name = "".join(ch for ch in p.get("name", "contact") if ch.isalnum()) or "contact"
    return Response("\r\n".join(lines) + "\r\n", media_type="text/vcard",
                    headers={"Content-Disposition": f'attachment; filename="{name}.vcf"'})


# ---------------------------------------------------------------- pairing the owner's phone
def valid_pairing(c, code: str):
    row = c.execute("SELECT * FROM pairings WHERE code_hash = ?", (security.token_hash(code),)).fetchone()
    if not row or row["used"] or row["expires"] < time.time():
        return None
    return row


@app.get("/pair/{code}", response_class=HTMLResponse)
def pair_page(request: Request, code: str):
    # GET only shows a confirm button: link previews (iMessage, mail scanners)
    # must not be able to use up the one-time link.
    if not pair_limiter.hit(client_ip(request)):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes.")
    with db.tx() as c:
        row = valid_pairing(c, code)
    if not row:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "pair.html", {"code": code, "label": row["label"]})


@app.post("/pair/{code}")
def pair_confirm(request: Request, code: str):
    ip = client_ip(request)
    if not pair_limiter.hit(ip):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes.")
    token, now = security.new_token(), time.time()
    with db.tx() as c:
        row = valid_pairing(c, code)
        if not row:
            raise HTTPException(404)
        c.execute("UPDATE pairings SET used = 1 WHERE id = ?", (row["id"],))
        c.execute(
            "INSERT INTO devices (id, label, token_hash, created, expires, last_seen, ip, ua)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (security.new_id(), row["label"], security.token_hash(token), now,
             now + settings.device_days * 86400, now, ip, request.headers.get("user-agent", "")[:200]),
        )
        db.audit(c, f"owner:{row['label']}", "device_paired", "", ip)
    notify.push("GHOST ID: device paired",
                f"'{row['label']}' can now control GHOST ID. If this wasn't you, run "
                f"'python manage.py unpair-all'.", priority="high", tags="key")
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(DEVICE_COOKIE, token, max_age=settings.device_days * 86400, httponly=True,
                    secure=settings.secure_cookies, samesite="lax", path="/")
    return resp


# ---------------------------------------------------------------- owner: command dashboard
@app.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request):
    me = require_owner(request)
    now = time.time()
    with db.tx() as c:
        owner = db.get_owner(c)
        devices = c.execute("SELECT * FROM devices WHERE revoked = 0 AND expires > ? ORDER BY created",
                            (now,)).fetchall()
        links = c.execute("SELECT * FROM resources ORDER BY id").fetchall()
        taps = c.execute("SELECT * FROM taps WHERE is_owner = 0 ORDER BY id DESC LIMIT 15").fetchall()
        audit = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 40").fetchall()
        tap_count = c.execute("SELECT COUNT(*) FROM taps WHERE is_owner = 0 AND ts > ?",
                              (now - 86400,)).fetchone()[0]
    return templates.TemplateResponse(request, "admin.html", {
        "owner": owner, "me": me, "devices": devices, "links": links, "taps": taps,
        "audit": audit, "tap_count": tap_count,
        "band_url": f"{settings.base_url}/t/{owner['band_id']}",
    })


@app.get("/admin/preview", response_class=HTMLResponse)
def preview(request: Request):
    """See the profile exactly as visitors see it."""
    require_owner(request)
    with db.tx() as c:
        owner = db.get_owner(c)
    return templates.TemplateResponse(request, "profile.html", {
        "profile": load_profile(), "band_id": owner["band_id"], "preview": True})


@app.post("/admin/devices/{device_id}/revoke")
def revoke_device(request: Request, device_id: str):
    me = require_owner(request)
    with db.tx() as c:
        row = c.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        if row:
            c.execute("UPDATE devices SET revoked = 1 WHERE id = ?", (device_id,))
            db.audit(c, f"owner:{me['label']}", "device_revoked", row["label"], client_ip(request))
    if row and row["id"] == me["id"]:
        resp = RedirectResponse("/", status_code=303)
        resp.delete_cookie(DEVICE_COOKIE, path="/")
        return resp
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/lockdown")
def lockdown(request: Request):
    """Kill switch: unpair every device except the one pressing the button."""
    me = require_owner(request)
    with db.tx() as c:
        n = c.execute("UPDATE devices SET revoked = 1 WHERE revoked = 0 AND id != ?", (me["id"],)).rowcount
        db.audit(c, f"owner:{me['label']}", "LOCKDOWN", f"unpaired {n} other devices", client_ip(request))
    notify.push("GHOST ID: lockdown", f"{n} other device(s) unpaired.", tags="rotating_light")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/links")
def add_link(request: Request, title: str = Form(...), url: str = Form(...)):
    me = require_owner(request)
    url = url.strip()
    if not url.startswith(("https://", "http://")):
        raise HTTPException(400, "Link must start with https://")
    with db.tx() as c:
        c.execute("INSERT INTO resources (title, url, note, created) VALUES (?,?,?,?)",
                  (title.strip()[:100], url[:500], "", time.time()))
        db.audit(c, f"owner:{me['label']}", "link_added", title[:100], client_ip(request))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/links/{link_id}/delete")
def delete_link(request: Request, link_id: int):
    me = require_owner(request)
    with db.tx() as c:
        c.execute("DELETE FROM resources WHERE id = ?", (link_id,))
        db.audit(c, f"owner:{me['label']}", "link_deleted", str(link_id), client_ip(request))
    return RedirectResponse("/admin", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True}
