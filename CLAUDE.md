# CLAUDE.md: GHOST ID project brief

Read this first. It is the full handoff for continuing GHOST ID in Claude Code.

## Who and what

- **Owner:** Abhay Bailey, Grade 12 student (Gurugram, India), builder of RAKSHAK-AI. Aiming for CS/AI/EE degrees abroad, so this is a **portfolio and exhibition project** and should look professional and be well documented.
- **GHOST ID** turns his Red Team Village DEF CON 34 NFC wristband (**NTAG213**: 144 bytes, ISO 14443A, rewritable, no crypto) into the physical trigger for a personal identity, access and automation ecosystem.
- **Core design rule (never break it):** *the band identifies the entry point, the owner's paired iPhone is the key, and the backend enforces permissions.* A tap alone must never grant anything, because the NTAG213 can be cloned with a Flipper Zero or Proxmark.
- **Owner decision (Nov 2026):** the band is for him. His paired phone tapping → full Command dashboard. Anyone else → public promotiona## Current state: Identity Core is DONE (tested)

Stack: **Python 3.12+, FastAPI, Jinja2 templates, SQLite (stdlib sqlite3)**, vanilla JS, no frontend framework. Push alerts via **ntfy** (free iOS app, topic in `.env`).

### What works
- `/t/<band_id>`: if the request carries a valid paired-device cookie → logs an `owner_tap` and redirects to `/admin`. Otherwise → public profile only (from `data/profile.json`, falling back to `profile.example.json`) with vCard download. Every tap is logged.
- Pairing: `manage.py pair [label]` prints `/pair/<code>` (one use, 10 min, rate limited). GET shows a confirm button (so link previews can't consume it); POST creates a device row and sets an httponly `ghost_device` cookie (365 days). Push alert on pairing.
- `/admin` (Command): visitor tap stats and a **recon card per visitor** (parsed device, coarse IP geo, browser fingerprint, and consent-only camera still + GPS), quick links (stored in the `resources` table), paired devices with Unpair, **LOCKDOWN** (unpairs all other devices), audit trail, `/admin/preview` of the public view. Every owner route returns the same 404 page to non-owners.
- `manage.py`: `init`, `pair`, `devices`, `unpair-all`, `wipe-recon`, `rotate-band`, `url`, `taps`.
- **Command analytics + actions (Nov 2026):** summary stats, 7-day chart, device breakdown, per-visitor detail page (`/admin/tap/<id>`), repeat/risk flags; dashboard actions: recon toggle (DB `config.recon_enabled` overrides `GHOST_RECON` via `recon_on()`), wipe recon, rotate band, generate pairing link, CSV/JSON export, DB-backed editable profile (`config.profile` overrides the profile file in `load_profile()`), notes, push.
- **Connect apps / automations (`app/automations.py`):** `webhooks` table; `fire(event,...)` posts to enabled hooks on `tap`/`photo`/`location`; kinds discord/slack/ntfy/home_assistant/generic. Outbound only, background threads, never raises. UI add/test/toggle/delete in Command. No command execution (honors the allow-list rule).
- **Visitor recon (red-team demo, owner decision Nov 2026):** on a visitor tap, `/t/<band>` embeds a one-time `token`; `app/static/recon.js` posts passive browser fingerprint signals to `POST /t/<band>/recon` (validated by token, 1 h TTL, visitor taps only, photo capped ~220 KB, always returns 200). Camera/GPS fire ONLY via on-page buttons that trigger the browser's native permission prompt; results are owner-only. A visible disclosure notice stays on the page. `app/geo.py` does best-effort IP->city in a background thread. Toggles: `GHOST_RECON=0` (whole demo off), `GHOST_GEO=0` (IP geo off). `manage.py wipe-recon` clears captures. Keep this consent-first and lawful (DPDP 2023 / GDPR): never make camera/GPS silent, never remove the notice, treat stored photos/coords as personal data.

### Security already implemented
Device tokens (256-bit) stored as SHA-256 hashes; httponly + SameSite=Lax cookies (Secure when the base URL is https); one-time expiring pairing codes, stored hashed; pairing rate limit of 10 per 15 min per IP; Origin-header CSRF check on all POSTs; strict CSP (no inline scripts, so JS lives in `/static`); X-Frame-Options DENY; no-store on owner and tap routes; random 96-bit band ID; owner routes indistinguishable from missing pages. No passwords exist.

5 min per IP with a push alert on failure; request rate limit of 5 per hour per IP; Origin-header CSRF check on all POSTs; strict CSP (no inline scripts, so JS lives in `/static`); X-Frame-Options DENY; no-store on private routes; random 96-bit band ID; pending requests expire after 15 min.

### File map
```
app/config.py     env settings (.env loader built in)
app/db.py         schema: owner, taps, resources (quick links), pairings, devices, audit
app/security.py   tokens, RateLimiter (in-memory)
app/notify.py     ntfy push (background thread, never raises)
app/geo.py        best-effort IP->city (background thread, never raises)
app/useragent.py  dependency-free UA summariser
app/static/recon.js  visitor fingerprint + consent camera/location beacon
app/automations.py   outbound webhooks on events (discord/slack/ntfy/HA/generic)
app/main.py       all routes + security middleware
app/templates/    base, home, profile, pair, message, admin, tap_detail, profile_edit
app/static/       style.css (dark "ghost" green theme)
manage.py         CLI
tests/test_flow.py  28 end-to-end tests (pytest + httpx TestClient)
pytest.ini        lets `pytest -q` import `app` from the repo root
Dockerfile        uvicorn with proxy headers; DB at /data
```

### Env vars (`.env`)
`GHOST_BASE_URL` (public https URL), `GHOST_TRUST_PROXY=1` behind a proxy, `NTFY_TOPIC` (long random secret), `GHOST_NOTIFY_TAPS` (0/1), optional `GHOST_DB`, `GHOST_PROFILE`, `GHOST_DEVICE_DAYS` (365), `GHOST_PAIR_MINUTES` (10), `GHOST_RECON` (1), `GHOST_GEO` (1), `GHOST_NOTIFY_TAPS` (0).

## Run

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt pytest httpx
cp .env.example .env && cp profile.example.json data/profile.json
python manage.py init
python manage.py pair              # open the printed link to pair this browser
uvicorn app.main:app --reload
pytest -q                          # must stay green
```

## Immediate to-dos (before writing the band)

1. Profile links are done (GitHub, LinkedIn in `profile.example.json`); email/phone intentionally blank.
2. **Deploy to a permanent https URL** on a host with a persistent disk (Railway + volume at `/data`, a VPS, or a Raspberry Pi + Cloudflare named tunnel) under a domain he controls, so the band never needs rewriting when hosts change. Don't write temporary tunnel URLs to the band.
3. Set `GHOST_BASE_URL`, then `python manage.py url`.
4. Write the band on iPhone with **NFC Tools**: Write → Add record → URL/URI → Write. Optionally set the NTAG213 write password (Other → Set password). **Never use "Lock tag"** (permanent).
5. Run `manage.py pair` on the server and open the link in **Safari** on his iPhone (NFC taps open Safari; Home Screen web apps have separate cookies). Subscribe to the ntfy topic.

## Roadmap (build in order, keep tests green, update README each phase)

| # | Phase | Notes |
|---|---|---|
| 2 | Harden access (**DEFERRED** by owner; do not start until he asks) | Optionally require a **passkey (WebAuthn)** on top of the device cookie for sensitive actions; add CSRF tokens on forms; protect the ntfy topic with auth or self-host ntfy; persist the rate limiter. |
| 3 | iPhone automation | Shortcuts NFC automations (owner phone only): open dashboard, Focus modes, call GHOST API endpoints with an owner API token. Add `/api` token auth for this. |
| 4 | ESP32 + NFC desk reader | ESP32 + PN532 (preferred, I2C) or RC522. Reads the band UID/URL and POSTs to `/api/reader/tap` with a per-reader secret (HMAC). Each reader has an ID so actions are context-aware (desk, lab). Optional OLED status. **He owns no reader hardware.** Build a software *virtual reader* (sends the same HMAC-signed tap) first; real ESP32 support is optional later. |
| 5 | PC launcher | Small Python agent on his PC that listens to the backend (websocket or long-poll) and runs whitelisted actions (open VS Code project, browser workspace, scripts). Only run pre-approved commands, never arbitrary ones. |
| 6 | AI assistant / daily brief | "Tap for daily brief": tasks, deadlines, project status summarised by an LLM; only approved tools and documents. |
| 7 | Vault | Time-limited file shares, per-resource permissions, remote revoke, download logging. |
| 8 | Unified command dashboard | Device status, readers online, active sessions, security notifications, IoT (Home Assistant, ESP32 lights). |

**Differentiator features to include along the way:** a built-in **clone-attack demo** for exhibitions (clone the band, tap it on another phone: only the public profile appears and Command stays 404); **NTAG424 DNA** support (SUN/SDM: a cryptographic code on every tap the server verifies) as the "secure band" (future: needs an NTAG424 band he does not own) next to the NTAG213 "insecure band"; context engine (reader + time + phone presence); tap-to-handoff phone → PC; lab session logger (tap in/out, log time and git commits).

## Constraints and conventions

- Keep dependencies minimal; prefer the stdlib (see `security.py`).
- No inline JS/CSS (CSP). Mobile-first UI; the visitor experience happens on phones.
- Every security-relevant action must write to `audit` via `db.audit(...)`.
- Anything that executes on his machines must be allow-listed. No remote arbitrary command execution.
- Only test against his own devices and accounts.
- Be honest in docs about limits: NTAG213 is clonable; whoever holds the unlocked paired phone has control; visitor tap logs contain IPs.
