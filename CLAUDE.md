# CLAUDE.md: GHOST ID project brief

Read this first. It is the full handoff for continuing GHOST ID in Claude Code.

## Who and what

- **Owner:** Abhay Bailey, Grade 12 student (Gurugram, India), builder of RAKSHAK-AI. Aiming for CS/AI/EE degrees abroad, so this is a **portfolio and exhibition project** and should look professional and be well documented.
- **GHOST ID** turns his Red Team Village DEF CON 34 NFC wristband (**NTAG213**: 144 bytes, ISO 14443A, rewritable, no crypto) into the physical trigger for a personal identity, access and automation ecosystem.
- **Core design rule (never break it):** *the band identifies the entry point, the owner's iPhone approves access, and the backend enforces permissions.* A tap alone must never grant anything, because the NTAG213 can be cloned with a Flipper Zero or Proxmark.
- **Guiding principle:** one integrated system with modules sharing one identity and one security core, not eight disconnected gimmicks.

## Current state: Phase 1 is DONE (tested)

Stack: **Python 3.12, FastAPI, Jinja2 templates, SQLite (stdlib sqlite3)**, vanilla JS, no frontend framework. Push alerts via **ntfy** (free iOS app, topic in `.env`).

### What works
- `/t/<band_id>`: public profile (from `data/profile.json`), projects, highlights, vCard download, "request private access" form. Every tap is logged. If the owner is logged in, it shows an owner banner linking to the dashboard.
- Access request → ntfy push to iPhone → owner approves in `/admin` with a duration (5/15/60/240/1440 min) and chooses resources → visitor's `/wait/<id>` page polls `/api/requests/<id>` and claims a guest session **once** → `/private` shows shared resources with a countdown.
- `/r/<id>` opens resources through the server (checked and logged), then redirects.
- `/admin` (Command dashboard): pending requests, active sessions with revoke, **PANIC** (revokes all guest sessions and denies pending/approved requests), vault resources add/delete, audit trail, 24 h tap count.
- `manage.py`: `init` (name, password, generates band ID, prints band URL), `password`, `rotate-band` (new band ID, kills guest sessions; use if the band is lost or cloned), `url`, `taps`.

### Security already implemented
scrypt password hashing; session tokens stored as SHA-256 hashes; httponly + SameSite=Lax cookies (Secure when the base URL is https); login rate limit of 5 per 15 min per IP with a push alert on failure; request rate limit of 5 per hour per IP; Origin-header CSRF check on all POSTs; strict CSP (no inline scripts, so JS lives in `/static`); X-Frame-Options DENY; no-store on private routes; random 96-bit band ID; pending requests expire after 15 min.

### File map
```
app/config.py     env settings (.env loader built in)
app/db.py         schema: owner, taps, resources, requests, sessions, audit
app/security.py   hashing, tokens, RateLimiter (in-memory)
app/notify.py     ntfy push (background thread, never raises)
app/main.py       all routes + security middleware
app/templates/    base, home, profile, wait, private, message, login, admin
app/static/       style.css (dark "ghost" green theme), wait.js, private.js
manage.py         CLI
tests/test_flow.py  6 end-to-end tests (pytest + httpx TestClient)
Dockerfile        uvicorn with proxy headers; DB at /data
```

### Env vars (`.env`)
`GHOST_BASE_URL` (public https URL), `GHOST_TRUST_PROXY=1` behind a proxy, `NTFY_TOPIC` (long random secret), `GHOST_NOTIFY_TAPS` (0/1), optional `GHOST_DB`, `GHOST_PROFILE`, `GHOST_OWNER_SESSION_HOURS`, `GHOST_REQUEST_TTL_MINUTES`.

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest httpx
cp .env.example .env && cp profile.example.json data/profile.json
python manage.py init
uvicorn app.main:app --reload      # http://localhost:8000/admin
pytest -q                          # must stay green
```

## Immediate to-dos (before writing the band)

1. Fill real links/email in `data/profile.json` (GitHub, LinkedIn and email are placeholders).
2. **Deploy to a permanent https URL** on a host with a persistent disk (Railway + volume at `/data`, a VPS, or a Raspberry Pi + Cloudflare named tunnel) under a domain he controls, so the band never needs rewriting when hosts change. Don't write temporary tunnel URLs to the band.
3. Set `GHOST_BASE_URL`, then `python manage.py url`.
4. Write the band on iPhone with **NFC Tools**: Write → Add record → URL/URI → Write. Optionally set the NTAG213 write password (Other → Set password). **Never use "Lock tag"** (permanent).
5. Subscribe to the ntfy topic on the iPhone; add `/admin` to the Home Screen.

## Roadmap (build in order, keep tests green, update README each phase)

| # | Phase | Notes |
|---|---|---|
| 2 | Harden access (**DEFERRED** by owner; do not start until he asks) | Replace password-only owner login with **passkeys (WebAuthn)**, keeping the password as fallback; add CSRF tokens on forms; protect the ntfy topic with auth or self-host ntfy; persist the rate limiter. |
| 3 | iPhone automation | Shortcuts NFC automations (owner phone only): open dashboard, Focus modes, call GHOST API endpoints with an owner API token. Add `/api` token auth for this. |
| 4 | ESP32 + NFC desk reader | ESP32 + PN532 (preferred, I2C) or RC522. Reads the band UID/URL and POSTs to `/api/reader/tap` with a per-reader secret (HMAC). Each reader has an ID so actions are context-aware (desk, lab). Optional OLED status. **He owns no reader hardware.** Build a software *virtual reader* (sends the same HMAC-signed tap) first; real ESP32 support is optional later. |
| 5 | PC launcher | Small Python agent on his PC that listens to the backend (websocket or long-poll) and runs whitelisted actions (open VS Code project, browser workspace, scripts). Only run pre-approved commands, never arbitrary ones. |
| 6 | AI assistant / daily brief | "Tap for daily brief": tasks, deadlines, project status summarised by an LLM; only approved tools and documents. |
| 7 | Vault | Time-limited file shares, per-resource permissions, remote revoke, download logging. |
| 8 | Unified command dashboard | Device status, readers online, active sessions, security notifications, IoT (Home Assistant, ESP32 lights). |

**Differentiator features to include along the way:** a built-in **clone-attack demo** for exhibitions (clone the band, show the system refusing without phone approval, audit log flags it); **NTAG424 DNA** support (SUN/SDM: a cryptographic code on every tap the server verifies) as the "secure band" (future: needs an NTAG424 band he does not own) next to the NTAG213 "insecure band"; context engine (reader + time + phone presence); tap-to-handoff phone → PC; lab session logger (tap in/out, log time and git commits).

## Constraints and conventions

- Keep dependencies minimal; prefer the stdlib (see `security.py`).
- No inline JS/CSS (CSP). Mobile-first UI; the visitor experience happens on phones.
- Every security-relevant action must write to `audit` via `db.audit(...)`.
- Anything that executes on his machines must be allow-listed. No remote arbitrary command execution.
- Only test against his own devices and accounts.
- Be honest in docs about limits: NTAG213 is clonable; shared content can be copied once viewed; revoke stops future access only.
