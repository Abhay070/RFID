# GHOST ID: Identity Core

**The band identifies the entry point, your paired iPhone is the key, and the backend enforces every permission.**

One band, two experiences:

| Who taps | What they get |
|---|---|
| **You, on your paired iPhone** | Straight into **Command**, your control dashboard |
| **Anyone else** | Your public promotional profile only: bio, projects, highlights, links and a Save contact card |

Visitors never see a login page, request form or any hint that Command exists. `/admin` answers *Not found* to them.

## What works now

| Feature | Where |
|---|---|
| Public profile with projects, highlights and links | `/t/<band-id>` |
| "Save contact" button (vCard) | `/t/<band-id>/contact.vcf` |
| Owner tap: your paired phone is sent straight to Command | `/t/<band-id>` → `/admin` |
| One-time phone pairing link, created only from the server | `manage.py pair` |
| **Who tapped your band**: a recon card per visitor (see below) | `/admin` |
| Quick links: your repos, docs and tools in one place | `/admin` |
| Paired devices list with Unpair, and a **LOCKDOWN** button that unpairs every other device | `/admin` |
| "Public view" preview of what visitors see | `/admin/preview` |
| Audit trail of every tap, pairing, unpair and lockdown | `/admin` |
| Push alert to your iPhone when a device is paired (and optionally on every visitor tap) | ntfy app |
| Band rotation if the band is lost or cloned | `manage.py rotate-band` |

**How your phone is recognised:** pairing stores a random 256-bit device token in a secure, httponly cookie on your phone (kept 365 days). The server stores only its SHA-256 hash. There is no password to guess or phish, and pairing links work once, expire after 10 minutes and are rate limited.

Other security: CSRF origin checks on every form, strict Content-Security-Policy (no inline scripts), no-store caching on owner pages, and a random 96-bit band ID that can't be guessed.

## Visitor recon (the red-team demo)

Each visitor tap builds a recon card in Command, visible to you only. It is designed to teach an exhibition audience how much a plain URL already gives away.

**Collected automatically** (the same signals every website receives, parsed for you): browser, OS and device; language; referrer; and a coarse city-level location from the IP (`GHOST_GEO=1`, best-effort, needs outbound https). A small on-page script also reports the browser's own fingerprint: screen and viewport, time zone, platform, CPU cores, RAM, GPU model, touch points, battery and network type. No permission prompt is involved, exactly like commercial ad trackers.

**Collected only with consent:** the profile shows **Allow camera** and **Share location** buttons. These trigger the *browser's own* permission prompt. A camera still (one downscaled frame) or precise GPS is captured only if the visitor accepts, and the result appears on your dashboard. This is a hard browser rule: neither can ever be taken silently.

**Ethics and law — read this before you demo it publicly.**
- The page carries a visible notice that it records analytics and may use the camera/location with permission. Keep that notice. Collecting this data covertly from people who haven't consented can breach India's DPDP Act 2023 and the EU GDPR, and most exhibition codes of conduct.
- Use it as an *awareness* demo: tell people what it does, ideally before they tap. The point lands harder when they understand it.
- Camera stills and precise coordinates of real people are personal data. Don't retain them longer than the demo, and don't publish them. `python manage.py wipe-recon` clears all captured recon.
- Only run this on bands and visitors in a setting where this is appropriate (your own stall, with signage). It is not a tool for tracking people who never agreed.

Turn the whole thing off with `GHOST_RECON=0`; turn only IP geolocation off with `GHOST_GEO=0`.

## Command center features

Your dashboard (your paired phone only) is a full control panel:

**Analytics**
- Summary tiles: total taps, unique IPs, unique devices, camera grabs, locations shared.
- A 7-day taps chart, device/browser breakdown and top locations.
- A **per-visitor detail page** (tap any row): full fingerprint, map, camera still, ISP, raw data.
- **Repeat-visitor and risk flags**: the same IP or device fingerprint tapping more than once is flagged, so you can spot a cloned tag or someone casing your band.

**Actions (no terminal needed)**
- Toggle the recon demo on/off, wipe all captured recon, rotate your band ID, and generate a fresh pairing link, all as buttons.
- **Export** every visitor tap and recon as CSV or JSON.
- **Edit your public profile** (bio, links, projects, highlights, contact) from the dashboard; changes show instantly.
- A private **notes** scratchpad and a **push-to-my-phone** box.

**Connect apps & automations**
- Add webhooks that fire on events (`tap`, `photo`, `location`, `pair`). Supported: Discord, Slack, ntfy, Home Assistant, and any generic JSON webhook (Zapier / IFTTT / n8n / Make).
- Example: "someone taps my band → ping my Discord channel and flash my Home Assistant lights."
- Outbound notifications only; GHOST ID never runs commands on any machine. Each webhook has Test / On-off / Delete.

## 1. Run it on your computer

```bash
cd ghost-id
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then edit .env
cp profile.example.json data/profile.json   # edit with your details
python manage.py init              # prints your band URL
python manage.py pair              # prints a one-time link to pair your phone
uvicorn app.main:app --reload
```

Open the pairing link in your browser, then open your band URL: you land in Command. Open the band URL in a private window to see the visitor view.

Run the tests with `pip install pytest httpx && pytest -q`.

## 2. Put it online (needed before writing the band)

Other people's phones have to be able to reach the server, so it needs a public **https** address.

- **Quick test (temporary):** run `cloudflared tunnel --url http://localhost:8000`. It gives you a random https URL that changes every time, so **don't write it to the band**. Use it only for testing.
- **Permanent:** deploy with the included `Dockerfile` to a host with a **persistent disk** (Railway with a volume mounted at `/data`, a small VPS, or a Raspberry Pi running Cloudflare Tunnel). Hosts that wipe the disk on redeploy will erase your database.
- Use a **domain you control** (e.g. `ghost.yourname.dev`). If you change hosts later, you only update the DNS and never need to rewrite the band.

Set `GHOST_BASE_URL` to the final https address, then run `python manage.py url` again.

## 3. Write the URL to your band (iPhone)

1. Install **NFC Tools** (by wakdev) from the App Store.
2. Open it and go to **Write → Add a record → URL / URI**.
3. Paste your band URL (e.g. `https://ghost.yourname.dev/t/AbC123...`) and tap **OK**.
4. Tap **Write**, then hold the band flat against the **top edge** of your iPhone until it confirms.
5. Test it: lock and unlock your phone, then tap the band to the top edge. A notification appears; tap it to open the profile.

The URL uses about 50 of the tag's 144 bytes, so there's plenty of room.

**Optional, protect against overwriting:** NFC Tools → **Other → Set password** sets the NTAG213's 32-bit write password. This stops casual overwrites at events. It is not strong security, but it's a good demo point.
**Never use "Lock tag".** It is permanent and you would never be able to rotate the URL.

## 4. Pair your iPhone

1. On the server, run `python manage.py pair iPhone`. It prints a link that works **once** and expires in **10 minutes**.
2. Open it in **Safari** on your iPhone and tap **Pair**. You land in Command.
3. Now tap the band on your iPhone and open the notification: it goes straight to Command.

Pair in Safari, because NFC taps open Safari. Home Screen web apps keep separate cookies.
**Lost or stolen phone:** run `python manage.py unpair-all` on the server, then pair your new phone.

## 5. Push alerts (optional)

Install **ntfy** from the App Store and subscribe to the exact topic in `NTFY_TOPIC`. Make it long and random, because anyone who knows the topic can read your alerts. You get an alert whenever a device is paired, and on every visitor tap if `GHOST_NOTIFY_TAPS=1`.

## 6. iPhone personal automations

Shortcuts app → **Automation → + → NFC** → scan your band → choose an action, for example turning on a Focus mode. This runs only on *your* phone and is the starting point for Phase 3 (iPhone automation).

## Honest limits (good to mention at exhibitions)

- The NTAG213 stores a plain URL with no cryptography, so it **can be cloned** with a Flipper Zero or Proxmark. That's why the band alone grants nothing: a cloned band tapped on someone else's phone shows only the public profile. Control needs the device token that exists only on your paired phone. If a band is cloned, run `rotate-band`.
- Upgrading to **NTAG424 DNA** adds a fresh cryptographic code on every tap (SUN/SDM) that the server can verify. This is planned for a later phase.
- Whoever holds your **unlocked, paired phone** has control, just like your email. Keep a phone passcode, and use `unpair-all` if it is lost.
- The visitor tap log records IP addresses and browser details. Mention this if you demo it publicly.

## Project layout

```
app/main.py       routes: tap, pairing, Command, links, devices, lockdown
app/db.py         SQLite schema
app/security.py   tokens, rate limiting
app/notify.py     iPhone push via ntfy
app/templates/    pages (profile, pair, admin, message)
manage.py         init / pair / devices / unpair-all / rotate-band / url / taps
tests/            end-to-end flow tests
```

## Next phases

2. Harden access: passkeys and more (deferred)  3. iPhone automation  4. Virtual NFC reader (ESP32 + PN532 optional later)  5. PC launcher  6. AI daily brief  7. Vault  8. Unified command dashboard
