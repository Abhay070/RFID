"""GHOST ID core server.

Design rule: the band identifies the entry point, the owner's iPhone approves
access, and this backend enforces every permission.
"""
import json
import time
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, notify, security
from .config import settings

BASE = Path(__file__).resolve().parent
ALLOWED_MINUTES = [5, 15, 60, 240, 1440]

app = FastAPI(title="GHOST ID", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

db.init()

login_limiter = security.RateLimiter(limit=5, window=15 * 60)
request_limiter = security.RateLimiter(limit=5, window=60 * 60)


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


def set_cookie(resp: Response, name: str, value: str, max_age: int) -> None:
    resp.set_cookie(name, value, max_age=max_age, httponly=True,
                    secure=settings.secure_cookies, samesite="lax", path="/")


def session_from_cookie(request: Request, cookie: str, kind: str):
    token = request.cookies.get(cookie)
    if not token:
        return None
    with db.tx() as c:
        row = c.execute(
            "SELECT * FROM sessions WHERE token_hash = ? AND kind = ?",
            (security.token_hash(token), kind),
        ).fetchone()
    if not row or row["revoked"] or row["expires"] < time.time():
        return None
    return row


def owner_session(request: Request):
    return session_from_cookie(request, "ghost_owner", "owner")


def guest_session(request: Request):
    return session_from_cookie(request, "ghost_guest", "guest")


def require_owner(request: Request):
    sess = owner_session(request)
    if not sess:
        raise HTTPException(status_code=303, headers={"Location": "/admin/login"})
    return sess


def require_band(band_id: str):
    with db.tx() as c:
        owner = db.get_owner(c)
    if not owner or not security_eq(band_id, owner["band_id"]):
        raise HTTPException(status_code=404)
    return owner


def security_eq(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode(), b.encode())


def expire_old_requests(c) -> None:
    cutoff = time.time() - settings.request_ttl_minutes * 60
    c.execute("UPDATE requests SET status = 'expired' WHERE status = 'pending' AND ts < ?", (cutoff,))


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
    if request.url.path.startswith(("/admin", "/private", "/api")):
        resp.headers["Cache-Control"] = "no-store"
    return resp


# ---------------------------------------------------------------- public: the tap
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(request, "home.html", {"profile": load_profile()})


@app.get("/t/{band_id}", response_class=HTMLResponse)
def tap(request: Request, band_id: str):
    owner = require_band(band_id)
    is_owner = owner_session(request) is not None
    ip, ua = client_ip(request), request.headers.get("user-agent", "")[:200]
    with db.tx() as c:
        c.execute("INSERT INTO taps (ts, band_id, ip, ua, is_owner) VALUES (?,?,?,?,?)",
                  (time.time(), band_id, ip, ua, int(is_owner)))
        db.audit(c, "owner" if is_owner else "visitor", "tap", ua[:80], ip)
    if settings.notify_taps and not is_owner:
        notify.push("GHOST ID: band tapped", f"Someone opened your profile ({ip}).",
                    click=f"{settings.base_url}/admin", tags="wave")
    return templates.TemplateResponse(request, "profile.html", {
        "profile": load_profile(), "band_id": band_id, "owner_name": owner["name"],
        "is_owner": is_owner, "has_guest": guest_session(request) is not None,
    })


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


# ---------------------------------------------------------------- private access request
@app.post("/t/{band_id}/request")
def request_access(request: Request, band_id: str,
                   name: str = Form(...), reason: str = Form("")):
    require_band(band_id)
    ip = client_ip(request)
    name, reason = name.strip()[:60], reason.strip()[:200]
    if not name:
        raise HTTPException(400, "Name is required")
    if not request_limiter.hit(ip):
        raise HTTPException(429, "Too many requests. Try again later.")
    req_id, visitor_token = security.new_id(), security.new_token()
    with db.tx() as c:
        c.execute(
            "INSERT INTO requests (id, ts, name, reason, ip, visitor_hash) VALUES (?,?,?,?,?,?)",
            (req_id, time.time(), name, reason, ip, security.token_hash(visitor_token)),
        )
        db.audit(c, f"visitor:{name}", "access_requested", reason, ip)
    notify.push("GHOST ID: access request",
                f"{name} wants private access. {reason}".strip(),
                click=f"{settings.base_url}/admin", priority="high", tags="key")
    resp = RedirectResponse(f"/wait/{req_id}", status_code=303)
    set_cookie(resp, "ghost_visitor", f"{req_id}.{visitor_token}",
               settings.request_ttl_minutes * 60 + 300)
    return resp


@app.get("/wait/{req_id}", response_class=HTMLResponse)
def wait(request: Request, req_id: str):
    return templates.TemplateResponse(request, "wait.html", {"req_id": req_id})


@app.get("/api/requests/{req_id}")
def request_status(request: Request, req_id: str):
    cookie = request.cookies.get("ghost_visitor", "")
    cookie_id, _, token = cookie.partition(".")
    if cookie_id != req_id or not token:
        return JSONResponse({"status": "unknown"}, status_code=403)
    with db.tx() as c:
        expire_old_requests(c)
        row = c.execute("SELECT * FROM requests WHERE id = ?", (req_id,)).fetchone()
        if not row or not security_eq(row["visitor_hash"], security.token_hash(token)):
            return JSONResponse({"status": "unknown"}, status_code=403)
        if row["status"] != "approved":
            return {"status": row["status"]}
        # Approved: hand the visitor a guest session exactly once.
        guest_token = security.new_token()
        now = time.time()
        expires = now + row["minutes"] * 60
        c.execute(
            "INSERT INTO sessions (id, kind, token_hash, label, created, expires, resource_ids, request_id, ip)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (security.new_id(), "guest", security.token_hash(guest_token), row["name"], now,
             expires, row["resource_ids"], req_id, client_ip(request)),
        )
        c.execute("UPDATE requests SET status = 'claimed' WHERE id = ?", (req_id,))
        db.audit(c, f"visitor:{row['name']}", "session_started",
                 f"{row['minutes']} min", client_ip(request))
    resp = JSONResponse({"status": "approved", "redirect": "/private"})
    set_cookie(resp, "ghost_guest", guest_token, row["minutes"] * 60)
    resp.delete_cookie("ghost_visitor", path="/")
    return resp


@app.get("/private", response_class=HTMLResponse)
def private(request: Request):
    sess = guest_session(request)
    if not sess:
        return templates.TemplateResponse(request, "message.html", {
            "title": "Session ended",
            "message": "This private session has expired or was revoked by the owner.",
        }, status_code=403)
    ids = [int(i) for i in (sess["resource_ids"] or "").split(",") if i]
    with db.tx() as c:
        rows = c.execute(
            f"SELECT * FROM resources WHERE id IN ({','.join('?' * len(ids))})", ids
        ).fetchall() if ids else []
    return templates.TemplateResponse(request, "private.html", {
        "session": sess, "resources": rows, "profile": load_profile(),
    })


@app.get("/r/{resource_id}")
def open_resource(request: Request, resource_id: int):
    """Resources are opened through here so every access is checked and logged."""
    sess = guest_session(request) or owner_session(request)
    if not sess:
        raise HTTPException(403, "No active session")
    if sess["kind"] == "guest":
        allowed = {int(i) for i in (sess["resource_ids"] or "").split(",") if i}
        if resource_id not in allowed:
            raise HTTPException(403, "Not shared with this session")
    with db.tx() as c:
        res = c.execute("SELECT * FROM resources WHERE id = ?", (resource_id,)).fetchone()
        if not res:
            raise HTTPException(404)
        db.audit(c, f"{sess['kind']}:{sess['label']}", "resource_opened", res["title"], client_ip(request))
    if res["url"]:
        return RedirectResponse(res["url"], status_code=303)
    return templates.TemplateResponse(request, "message.html",
                                      {"title": res["title"], "message": res["note"] or ""})


# ---------------------------------------------------------------- owner: login
@app.get("/admin/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/admin/login", response_class=HTMLResponse)
def login(request: Request, password: str = Form(...)):
    ip = client_ip(request)
    if not login_limiter.hit(ip):
        with db.tx() as c:
            db.audit(c, "unknown", "login_rate_limited", "", ip)
        return templates.TemplateResponse(request, "login.html",
                                          {"error": "Too many attempts. Wait 15 minutes."}, status_code=429)
    with db.tx() as c:
        owner = db.get_owner(c)
        if not owner or not security.verify_password(password, owner["pw_hash"]):
            db.audit(c, "unknown", "login_failed", "", ip)
            failed = True
        else:
            failed = False
            token = security.new_token()
            now = time.time()
            c.execute(
                "INSERT INTO sessions (id, kind, token_hash, label, created, expires, ip)"
                " VALUES (?,?,?,?,?,?,?)",
                (security.new_id(), "owner", security.token_hash(token), "owner", now,
                 now + settings.owner_session_hours * 3600, ip),
            )
            db.audit(c, "owner", "login", request.headers.get("user-agent", "")[:80], ip)
    if failed:
        notify.push("GHOST ID: failed login", f"Wrong password attempt from {ip}.",
                    priority="high", tags="warning")
        return templates.TemplateResponse(request, "login.html",
                                          {"error": "Wrong password."}, status_code=401)
    resp = RedirectResponse("/admin", status_code=303)
    set_cookie(resp, "ghost_owner", token, settings.owner_session_hours * 3600)
    return resp


@app.post("/admin/logout")
def logout(request: Request):
    sess = owner_session(request)
    if sess:
        with db.tx() as c:
            c.execute("UPDATE sessions SET revoked = 1 WHERE id = ?", (sess["id"],))
            db.audit(c, "owner", "logout", "", client_ip(request))
    resp = RedirectResponse("/admin/login", status_code=303)
    resp.delete_cookie("ghost_owner", path="/")
    return resp


# ---------------------------------------------------------------- owner: command dashboard
@app.get("/admin", response_class=HTMLResponse)
def dashboard(request: Request):
    require_owner(request)
    now = time.time()
    with db.tx() as c:
        expire_old_requests(c)
        owner = db.get_owner(c)
        pending = c.execute("SELECT * FROM requests WHERE status = 'pending' ORDER BY ts DESC").fetchall()
        sessions = c.execute(
            "SELECT * FROM sessions WHERE revoked = 0 AND expires > ? ORDER BY created DESC", (now,)
        ).fetchall()
        resources = c.execute("SELECT * FROM resources ORDER BY id").fetchall()
        audit = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 40").fetchall()
        tap_count = c.execute("SELECT COUNT(*) FROM taps WHERE ts > ?", (now - 86400,)).fetchone()[0]
    return templates.TemplateResponse(request, "admin.html", {
        "owner": owner, "pending": pending, "sessions": sessions, "resources": resources,
        "audit": audit, "tap_count": tap_count, "minutes": ALLOWED_MINUTES,
        "band_url": f"{settings.base_url}/t/{owner['band_id']}", "now": now,
    })


@app.post("/admin/requests/{req_id}/approve")
async def approve(request: Request, req_id: str):
    require_owner(request)
    form = await request.form()
    minutes = int(form.get("minutes", 15))
    if minutes not in ALLOWED_MINUTES:
        raise HTTPException(400, "Invalid duration")
    resource_ids = ",".join(str(int(r)) for r in form.getlist("resource"))
    with db.tx() as c:
        row = c.execute("SELECT * FROM requests WHERE id = ? AND status = 'pending'", (req_id,)).fetchone()
        if row:
            c.execute(
                "UPDATE requests SET status = 'approved', minutes = ?, resource_ids = ?, decided_ts = ? WHERE id = ?",
                (minutes, resource_ids, time.time(), req_id),
            )
            db.audit(c, "owner", "access_approved",
                     f"{row['name']} for {minutes} min, resources [{resource_ids}]", client_ip(request))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/requests/{req_id}/deny")
def deny(request: Request, req_id: str):
    require_owner(request)
    with db.tx() as c:
        row = c.execute("SELECT * FROM requests WHERE id = ? AND status = 'pending'", (req_id,)).fetchone()
        if row:
            c.execute("UPDATE requests SET status = 'denied', decided_ts = ? WHERE id = ?", (time.time(), req_id))
            db.audit(c, "owner", "access_denied", row["name"], client_ip(request))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/sessions/{session_id}/revoke")
def revoke(request: Request, session_id: str):
    me = require_owner(request)
    with db.tx() as c:
        row = c.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row:
            c.execute("UPDATE sessions SET revoked = 1 WHERE id = ?", (session_id,))
            db.audit(c, "owner", "session_revoked", f"{row['kind']}:{row['label']}", client_ip(request))
    if row and row["id"] == me["id"]:
        return RedirectResponse("/admin/login", status_code=303)
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/panic")
def panic(request: Request):
    """Kill switch: end every guest session and reject every pending request."""
    require_owner(request)
    with db.tx() as c:
        n = c.execute("UPDATE sessions SET revoked = 1 WHERE kind = 'guest' AND revoked = 0").rowcount
        m = c.execute("UPDATE requests SET status = 'denied', decided_ts = ? WHERE status IN ('pending','approved')",
                      (time.time(),)).rowcount
        db.audit(c, "owner", "PANIC", f"revoked {n} sessions, denied {m} requests", client_ip(request))
    notify.push("GHOST ID: panic mode", f"All guest access ended ({n} sessions).", tags="rotating_light")
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/resources")
def add_resource(request: Request, title: str = Form(...), url: str = Form(""), note: str = Form("")):
    require_owner(request)
    url = url.strip()
    if url and not url.startswith(("https://", "http://")):
        raise HTTPException(400, "Link must start with https://")
    with db.tx() as c:
        c.execute("INSERT INTO resources (title, url, note, created) VALUES (?,?,?,?)",
                  (title.strip()[:100], url[:500], note.strip()[:1000], time.time()))
        db.audit(c, "owner", "resource_added", title[:100], client_ip(request))
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/resources/{resource_id}/delete")
def delete_resource(request: Request, resource_id: int):
    require_owner(request)
    with db.tx() as c:
        c.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
        db.audit(c, "owner", "resource_deleted", str(resource_id), client_ip(request))
    return RedirectResponse("/admin", status_code=303)


@app.get("/healthz")
def healthz():
    return {"ok": True}
