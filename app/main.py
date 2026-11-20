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

from . import automations, db, geo, notify, security, useragent
from .config import settings

BASE = Path(__file__).resolve().parent
DEVICE_COOKIE = "ghost_device"
RECON_TTL = 3600          # a beacon token is accepted for 1 hour after the tap
MAX_PHOTO = 300_000       # ~220 KB data URL cap for a consent camera still

app = FastAPI(title="GHOST ID", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

db.init()
db.ensure_owner()   # seed from env on first boot (hosted instances without a shell)

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
    # A profile edited from Command (stored in the DB) wins; then the profile
    # file; then the bundled example.
    with db.tx() as c:
        saved = db.get_config(c, "profile")
    if saved:
        try:
            return json.loads(saved)
        except ValueError:
            pass
    path = Path(settings.profile_path)
    if not path.exists():
        path = BASE.parent / "profile.example.json"
    return json.loads(path.read_text(encoding="utf-8"))


def recon_on() -> bool:
    """Effective recon flag: a Command toggle overrides the GHOST_RECON env default."""
    with db.tx() as c:
        override = db.get_config(c, "recon_enabled")
    if override in ("0", "1"):
        return override == "1"
    return settings.recon_enabled


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
    if request.url.path.startswith(("/admin", "/pair", "/setup", "/t/")):
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
    lang = request.headers.get("accept-language", "")[:120]
    referer = request.headers.get("referer", "")[:300]
    token = security.new_token()
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO taps (ts, band_id, ip, ua, is_owner, lang, referer, token)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (time.time(), band_id, ip, ua, int(dev is not None), lang, referer, token),
        )
        tap_id = cur.lastrowid
        if dev:
            db.audit(c, f"owner:{dev['label']}", "owner_tap", "", ip)
        else:
            db.audit(c, "visitor", "tap", useragent.summarise(ua), ip)
    if dev:
        return RedirectResponse("/admin", status_code=303)
    geo.lookup_async(tap_id, ip)
    device = useragent.summarise(ua)
    if settings.notify_taps:
        notify.push("GHOST ID: band tapped", f"{device} opened your profile ({ip}).", tags="wave")
    automations.fire("tap", "Band tapped", f"{device} from {ip} opened your profile.",
                     {"ip": ip, "device": device, "tap_id": tap_id})
    # token lets this visitor's browser post its own recon back; owner-only to view.
    recon_token = token if recon_on() else None
    return templates.TemplateResponse(request, "profile.html", {
        "profile": load_profile(), "band_id": band_id, "recon_token": recon_token})


@app.post("/t/{band_id}/recon")
async def recon(request: Request, band_id: str):
    """Receive the browser beacon for a tap. Deliberately answers 200 to
    everything so a probe learns nothing about what was or wasn't stored."""
    require_band(band_id)
    if not recon_on():
        return JSONResponse({"ok": True})
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"ok": True})
    token = str(body.get("token", ""))[:200]
    if not token:
        return JSONResponse({"ok": True})
    now = time.time()
    with db.tx() as c:
        row = c.execute("SELECT * FROM taps WHERE token = ? AND band_id = ?",
                        (token, band_id)).fetchone()
        if not row or row["is_owner"] or (now - row["ts"]) > RECON_TTL:
            return JSONResponse({"ok": True})

        fp = body.get("fp")
        fp_json = json.dumps(fp)[:4000] if isinstance(fp, dict) else None

        photo = body.get("photo")
        if not (isinstance(photo, str) and photo.startswith("data:image/") and len(photo) <= MAX_PHOTO):
            photo = None

        def num(v):
            return float(v) if isinstance(v, (int, float)) else None
        lat, lon, acc = num(body.get("lat")), num(body.get("lon")), num(body.get("acc"))

        c.execute(
            "INSERT INTO recon (tap_id, ts, ip, fp, photo, lat, lon, acc)"
            " VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(tap_id) DO UPDATE SET ts = excluded.ts,"
            "   fp = COALESCE(excluded.fp, recon.fp),"
            "   photo = COALESCE(excluded.photo, recon.photo),"
            "   lat = COALESCE(excluded.lat, recon.lat),"
            "   lon = COALESCE(excluded.lon, recon.lon),"
            "   acc = COALESCE(excluded.acc, recon.acc)",
            (row["id"], now, row["ip"], fp_json, photo, lat, lon, acc),
        )
        detail = ", ".join(k for k, v in
                           (("fingerprint", fp_json), ("photo", photo), ("location", lat)) if v)
        db.audit(c, "visitor", "recon_captured", detail, row["ip"])
    if photo:
        automations.fire("photo", "Camera captured",
                         f"A visitor from {row['ip']} shared their camera.",
                         {"ip": row["ip"], "tap_id": row["id"]})
    if lat is not None:
        automations.fire("location", "Location shared",
                         f"A visitor from {row['ip']} shared location {lat:.4f},{lon:.4f}.",
                         {"ip": row["ip"], "lat": lat, "lon": lon, "tap_id": row["id"]})
    return JSONResponse({"ok": True})


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
@app.get("/setup/{key:path}", response_class=HTMLResponse)
def setup_page(request: Request, key: str):
    """Pair a phone from the browser using the secret GHOST_SETUP_KEY. For hosts
    where you can't run 'manage.py pair'. GET only shows a button (so link
    previews can't use it); the POST does the pairing."""
    if not pair_limiter.hit(client_ip(request)):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes.")
    if not settings.setup_key or not same(key, settings.setup_key):
        raise HTTPException(404)
    return templates.TemplateResponse(request, "pair.html", {"code": None, "label": "this phone",
                                                             "setup_key": key})


@app.post("/setup/{key:path}")
def setup_confirm(request: Request, key: str):
    ip = client_ip(request)
    if not pair_limiter.hit(ip):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes.")
    if not settings.setup_key or not same(key, settings.setup_key):
        raise HTTPException(404)
    token, now = security.new_token(), time.time()
    with db.tx() as c:
        c.execute(
            "INSERT INTO devices (id, label, token_hash, created, expires, last_seen, ip, ua)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (security.new_id(), "setup link", security.token_hash(token), now,
             now + settings.device_days * 86400, now, ip, request.headers.get("user-agent", "")[:200]),
        )
        db.audit(c, "owner", "device_paired", "via setup link", ip)
    notify.push("GHOST ID: device paired", "A phone was paired via the setup link.",
                priority="high", tags="key")
    resp = RedirectResponse("/admin", status_code=303)
    resp.set_cookie(DEVICE_COOKIE, token, max_age=settings.device_days * 86400, httponly=True,
                    secure=settings.secure_cookies, samesite="lax", path="/")
    return resp


FP_LABELS = (("screen", "Screen"), ("viewport", "Viewport"), ("tz", "Time zone"),
             ("langs", "Languages"), ("platform", "Platform"), ("cores", "CPU cores"),
             ("memory", "RAM (GB)"), ("gpu", "GPU"), ("touch", "Touch points"),
             ("battery", "Battery"), ("network", "Network"))


def _fp_hash(fp_json: str | None) -> str | None:
    if not fp_json:
        return None
    try:
        fp = json.loads(fp_json)
    except ValueError:
        return None
    key = json.dumps({k: fp.get(k) for k in ("screen", "tz", "platform", "gpu", "langs")},
                     sort_keys=True)
    return security.token_hash(key)[:12]


def _recon_view(r, ip_counts=None, fp_counts=None) -> dict:
    """Flatten a tap + its recon into display-ready fields, with repeat/risk flags."""
    g = json.loads(r["geo"]) if r["geo"] else {}
    place = ", ".join(v for v in (g.get("city"), g.get("region"), g.get("country")) if v)
    fp = json.loads(r["fp"]) if r["fp"] else {}
    rows = []
    for key, label in FP_LABELS:
        if fp.get(key) not in (None, "", []):
            val = fp[key]
            rows.append((label, ", ".join(map(str, val)) if isinstance(val, list) else str(val)))
    maps = f"https://www.google.com/maps?q={r['lat']},{r['lon']}" if r["lat"] is not None else None
    fph = _fp_hash(r["fp"])
    flags = []
    if ip_counts and ip_counts.get(r["ip"], 0) > 1:
        flags.append(f"repeat IP ×{ip_counts[r['ip']]}")
    if fp_counts and fph and fp_counts.get(fph, 0) > 1:
        flags.append(f"repeat device ×{fp_counts[fph]}")
    if ip_counts and ip_counts.get(r["ip"], 0) >= 4:
        flags.append("frequent IP")
    return {
        "id": r["id"], "ts": r["ts"], "ip": r["ip"], "device": useragent.summarise(r["ua"] or ""),
        "ua": r["ua"] or "", "lang": r["lang"] or "", "referer": r["referer"] or "",
        "place": place, "org": g.get("org"), "fp": rows, "photo": r["photo"],
        "lat": r["lat"], "lon": r["lon"], "acc": r["acc"], "maps": maps, "flags": flags,
    }


def _analytics(c) -> dict:
    """Aggregate stats for the summary panel."""
    now = time.time()
    vt = c.execute("SELECT t.ip, t.ua, t.geo, r.fp FROM taps t LEFT JOIN recon r ON r.tap_id = t.id"
                   " WHERE t.is_owner = 0").fetchall()
    ip_counts, fp_counts, devices, places = {}, {}, {}, {}
    for r in vt:
        ip_counts[r["ip"]] = ip_counts.get(r["ip"], 0) + 1
        h = _fp_hash(r["fp"])
        if h:
            fp_counts[h] = fp_counts.get(h, 0) + 1
        d = useragent.summarise(r["ua"] or "")
        devices[d] = devices.get(d, 0) + 1
        if r["geo"]:
            try:
                g = json.loads(r["geo"])
                name = ", ".join(v for v in (g.get("city"), g.get("country")) if v)
                if name:
                    places[name] = places.get(name, 0) + 1
            except ValueError:
                pass
    photos = c.execute("SELECT COUNT(*) FROM recon WHERE photo IS NOT NULL").fetchone()[0]
    locs = c.execute("SELECT COUNT(*) FROM recon WHERE lat IS NOT NULL").fetchone()[0]
    # 7-day daily buckets
    days = []
    for i in range(6, -1, -1):
        start = now - (i + 1) * 86400
        end = now - i * 86400
        n = c.execute("SELECT COUNT(*) FROM taps WHERE is_owner = 0 AND ts >= ? AND ts < ?",
                      (start, end)).fetchone()[0]
        days.append((time.strftime("%a", time.localtime(end - 1)), n))
    top = lambda d: sorted(d.items(), key=lambda kv: -kv[1])[:5]
    peak = max((n for _, n in days), default=0) or 1
    return {
        "total": len(vt), "unique_ips": len(ip_counts), "unique_devices": len(fp_counts),
        "photos": photos, "locations": locs,
        "device_breakdown": top(devices), "top_places": top(places),
        "days": [(label, n, round(100 * n / peak)) for label, n in days],
        "ip_counts": ip_counts, "fp_counts": fp_counts,
    }


@app.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request):
    me = require_owner(request)
    now = time.time()
    link = request.query_params.get("pair_link", "")
    with db.tx() as c:
        owner = db.get_owner(c)
        devices = c.execute("SELECT * FROM devices WHERE revoked = 0 AND expires > ? ORDER BY created",
                            (now,)).fetchall()
        links = c.execute("SELECT * FROM resources ORDER BY id").fetchall()
        stats = _analytics(c)
        rows = c.execute(
            "SELECT t.*, r.fp, r.photo, r.lat, r.lon, r.acc FROM taps t"
            " LEFT JOIN recon r ON r.tap_id = t.id"
            " WHERE t.is_owner = 0 ORDER BY t.id DESC LIMIT 20").fetchall()
        audit = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 30").fetchall()
        notes = c.execute("SELECT * FROM notes ORDER BY id DESC LIMIT 30").fetchall()
        hooks = c.execute("SELECT * FROM webhooks ORDER BY id").fetchall()
    taps = [_recon_view(r, stats["ip_counts"], stats["fp_counts"]) for r in rows]
    return templates.TemplateResponse(request, "admin.html", {
        "owner": owner, "me": me, "devices": devices, "links": links, "taps": taps,
        "audit": audit, "stats": stats, "notes": notes, "hooks": hooks,
        "recon_on": recon_on(), "pair_link": link,
        "webhook_kinds": automations.KINDS, "webhook_events": automations.EVENTS,
        "band_url": f"{settings.base_url}/t/{owner['band_id']}",
    })


@app.get("/admin/tap/{tap_id}", response_class=HTMLResponse)
def tap_detail(request: Request, tap_id: int):
    require_owner(request)
    with db.tx() as c:
        r = c.execute("SELECT t.*, r.fp, r.photo, r.lat, r.lon, r.acc FROM taps t"
                      " LEFT JOIN recon r ON r.tap_id = t.id WHERE t.id = ? AND t.is_owner = 0",
                      (tap_id,)).fetchone()
        if not r:
            raise HTTPException(404)
        stats = _analytics(c)
    view = _recon_view(r, stats["ip_counts"], stats["fp_counts"])
    view["fp_raw"] = json.dumps(json.loads(r["fp"]), indent=2) if r["fp"] else ""
    return templates.TemplateResponse(request, "tap_detail.html", {"t": view})


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


# ---------------------------------------------------------------- actions: toggles
@app.post("/admin/recon/toggle")
def toggle_recon(request: Request):
    me = require_owner(request)
    with db.tx() as c:
        new = "0" if recon_on() else "1"
        db.set_config(c, "recon_enabled", new)
        db.audit(c, f"owner:{me['label']}", "recon_toggled", "on" if new == "1" else "off",
                 client_ip(request))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/wipe-recon")
def wipe_recon(request: Request):
    me = require_owner(request)
    with db.tx() as c:
        n = c.execute("DELETE FROM recon").rowcount
        db.audit(c, f"owner:{me['label']}", "recon_wiped", f"{n} records", client_ip(request))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/pair-link")
def new_pair_link(request: Request):
    me = require_owner(request)
    code, now = security.new_token(), time.time()
    with db.tx() as c:
        c.execute("INSERT INTO pairings (code_hash, label, created, expires) VALUES (?,?,?,?)",
                  (security.token_hash(code), "iPhone", now, now + settings.pair_minutes * 60))
        db.audit(c, f"owner:{me['label']}", "pairing_link_created", "", client_ip(request))
    url = f"{settings.base_url}/pair/{code}"
    return RedirectResponse(f"/admin?pair_link={url}", status_code=303)


@app.post("/admin/rotate-band")
def rotate_band(request: Request):
    me = require_owner(request)
    new_band = security.new_band_id()
    with db.tx() as c:
        c.execute("UPDATE owner SET band_id = ? WHERE id = 1", (new_band,))
        db.audit(c, f"owner:{me['label']}", "band_rotated", new_band, client_ip(request))
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------- actions: export
@app.get("/admin/export.json")
def export_json(request: Request):
    require_owner(request)
    with db.tx() as c:
        rows = c.execute("SELECT t.*, r.fp, r.lat, r.lon, r.acc, (r.photo IS NOT NULL) AS has_photo"
                         " FROM taps t LEFT JOIN recon r ON r.tap_id = t.id WHERE t.is_owner = 0"
                         " ORDER BY t.id").fetchall()
    data = [dict(r) for r in rows]
    return Response(json.dumps(data, indent=2), media_type="application/json",
                    headers={"Content-Disposition": 'attachment; filename="ghost-taps.json"'})


@app.get("/admin/export.csv")
def export_csv(request: Request):
    require_owner(request)
    import csv
    import io
    with db.tx() as c:
        rows = c.execute("SELECT t.id, t.ts, t.ip, t.ua, t.lang, t.geo, r.lat, r.lon,"
                         " (r.photo IS NOT NULL) AS has_photo FROM taps t"
                         " LEFT JOIN recon r ON r.tap_id = t.id WHERE t.is_owner = 0"
                         " ORDER BY t.id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["id", "time", "ip", "device", "user_agent", "language", "location",
                "lat", "lon", "camera"])
    for r in rows:
        g = json.loads(r["geo"]) if r["geo"] else {}
        place = ", ".join(v for v in (g.get("city"), g.get("country")) if v)
        w.writerow([r["id"], fmt_time(r["ts"]), r["ip"], useragent.summarise(r["ua"] or ""),
                    r["ua"], r["lang"], place, r["lat"], r["lon"], "yes" if r["has_photo"] else ""])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="ghost-taps.csv"'})


# ---------------------------------------------------------------- actions: editable profile
@app.get("/admin/profile", response_class=HTMLResponse)
def profile_edit(request: Request):
    require_owner(request)
    p = load_profile()
    text = {
        "links": "\n".join(f"{l.get('label','')}|{l.get('url','')}" for l in p.get("links", [])),
        "projects": "\n".join(f"{x.get('name','')}|{x.get('desc','')}" for x in p.get("projects", [])),
        "achievements": "\n".join(p.get("achievements", [])),
    }
    return templates.TemplateResponse(request, "profile_edit.html", {"p": p, "text": text})


@app.post("/admin/profile")
async def profile_save(request: Request):
    me = require_owner(request)
    f = await request.form()

    def lines(key):
        return [ln.strip() for ln in str(f.get(key, "")).splitlines() if ln.strip()]
    profile = {
        "name": str(f.get("name", "")).strip()[:80],
        "tagline": str(f.get("tagline", "")).strip()[:120],
        "location": str(f.get("location", "")).strip()[:80],
        "bio": str(f.get("bio", "")).strip()[:500],
        "links": [{"label": a.split("|")[0].strip(), "url": a.split("|", 1)[1].strip()}
                  for a in lines("links") if "|" in a and a.split("|", 1)[1].strip().startswith("http")],
        "projects": [{"name": a.split("|")[0].strip(), "desc": a.split("|", 1)[1].strip()}
                     for a in lines("projects") if "|" in a],
        "achievements": lines("achievements"),
        "contact": {"email": str(f.get("email", "")).strip()[:120],
                    "phone": str(f.get("phone", "")).strip()[:40]},
    }
    with db.tx() as c:
        db.set_config(c, "profile", json.dumps(profile))
        db.audit(c, f"owner:{me['label']}", "profile_edited", "", client_ip(request))
    return RedirectResponse("/admin/profile", status_code=303)


# ---------------------------------------------------------------- actions: notes & push
@app.post("/admin/notes")
def add_note(request: Request, text: str = Form(...)):
    me = require_owner(request)
    text = text.strip()[:500]
    if text:
        with db.tx() as c:
            c.execute("INSERT INTO notes (ts, text) VALUES (?, ?)", (time.time(), text))
            db.audit(c, f"owner:{me['label']}", "note_added", "", client_ip(request))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/notes/{note_id}/delete")
def delete_note(request: Request, note_id: int):
    require_owner(request)
    with db.tx() as c:
        c.execute("DELETE FROM notes WHERE id = ?", (note_id,))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/push")
def send_push(request: Request, message: str = Form(...)):
    me = require_owner(request)
    message = message.strip()[:200]
    if message:
        notify.push("GHOST ID note", message, tags="memo")
        automations.fire("tap", "Note from Command", message, {"manual": True})
        with db.tx() as c:
            db.audit(c, f"owner:{me['label']}", "push_sent", message[:60], client_ip(request))
    return RedirectResponse("/admin", status_code=303)


# ---------------------------------------------------------------- actions: connect apps (webhooks)
@app.post("/admin/webhooks")
async def add_webhook(request: Request):
    me = require_owner(request)
    f = await request.form()
    kind = str(f.get("kind", "")).strip()
    url = str(f.get("url", "")).strip()
    name = str(f.get("name", "")).strip()[:60] or kind
    events = [e for e in automations.EVENTS if f.get(f"event_{e}")]
    if kind not in automations.KINDS or not url.startswith(("https://", "http://")) or not events:
        raise HTTPException(400, "Pick a type, a valid https URL, and at least one event.")
    with db.tx() as c:
        c.execute("INSERT INTO webhooks (kind, name, url, events, created) VALUES (?,?,?,?,?)",
                  (kind, name, url[:500], ",".join(events), time.time()))
        db.audit(c, f"owner:{me['label']}", "webhook_added", f"{kind}: {name}", client_ip(request))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/webhooks/{hook_id}/delete")
def delete_webhook(request: Request, hook_id: int):
    require_owner(request)
    with db.tx() as c:
        c.execute("DELETE FROM webhooks WHERE id = ?", (hook_id,))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/webhooks/{hook_id}/toggle")
def toggle_webhook(request: Request, hook_id: int):
    require_owner(request)
    with db.tx() as c:
        c.execute("UPDATE webhooks SET enabled = 1 - enabled WHERE id = ?", (hook_id,))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/webhooks/{hook_id}/test")
def test_webhook(request: Request, hook_id: int):
    require_owner(request)
    with db.tx() as c:
        h = c.execute("SELECT * FROM webhooks WHERE id = ?", (hook_id,)).fetchone()
    if h:
        automations._send(h["id"], h["kind"], h["url"], "GHOST ID test",
                          "This is a test message from your Command center.", {"event": "test"})
    return RedirectResponse("/admin", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True}
