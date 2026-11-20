# GHOST ID: Phase 1 (Identity + Access Core)

**The band identifies the entry point, your iPhone approves access, and the backend enforces permissions.**

## What works now

| Feature | Where |
|---|---|
| Tap the band to open your public profile, projects and highlights | `/t/<band-id>` |
| "Save contact" button (vCard) | `/t/<band-id>/contact.vcf` |
| Visitor requests private access, and your iPhone gets a push alert | ntfy app |
| You approve from your phone with a time limit (5 min to 24 h) and choose which resources to share | `/admin` |
| Visitor gets a private page with a live countdown that closes when time runs out | `/private` |
| Revoke any session at any time | `/admin` |
| **PANIC** button that ends all guest access at once | `/admin` |
| Owner tap detection: when you're logged in, tapping your own band opens the dashboard shortcut | `/t/<band-id>` |
| Audit trail of every tap, request, approval, login, failed login and resource opened | `/admin` |
| Band rotation: if the band is lost or cloned, issue a new ID and the old URL stops working | `manage.py rotate-band` |

Security built in: scrypt password hashing, hashed session tokens, login rate limiting with push alerts on failed attempts, CSRF origin checks, strict Content-Security-Policy, no-store caching on private pages, and a random 96-bit band ID that can't be guessed.

## 1. Run it on your computer

```bash
cd ghost-id
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then edit .env
cp profile.example.json data/profile.json   # edit with your details
python manage.py init              # sets your password and prints your band URL
uvicorn app.main:app --reload
```

Open http://localhost:8000/admin and log in. Run `python manage.py url` to see your band URL.

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

## 4. Get approval alerts on your iPhone

1. Install **ntfy** from the App Store.
2. Subscribe to the exact topic you set in `NTFY_TOPIC`. Make it long and random, because anyone who knows the topic can read your alerts.
3. Log in to `/admin` once in Safari on your iPhone. Use Share → **Add to Home Screen** to get a one-tap command dashboard.

Now when someone requests access, you get a push alert. Tap it, choose duration and resources, and approve.

## 5. iPhone personal automations (owner side)

Shortcuts app → **Automation → + → NFC** → scan your band → choose an action, for example "Open URL" pointing to your `/admin`, or turning on a Focus mode.
This runs only on *your* phone, which makes it the starting point for Phase 3 (iPhone automation).

## Honest limits (good to mention at exhibitions)

- The NTAG213 stores a plain URL with no cryptography, so it **can be cloned** with a Flipper Zero or Proxmark. That's why a tap only opens the door to a request, and a real decision always needs your phone. If a band is cloned, run `rotate-band`.
- Upgrading to **NTAG424 DNA** adds a fresh cryptographic code on every tap (SUN/SDM) that the server can verify. This is planned for a later phase.
- Once someone opens a shared link, they can copy what they see. Revoking access stops future access but can't erase copies.

## Project layout

```
app/main.py       routes: tap, request, approve, sessions, panic, audit
app/db.py         SQLite schema
app/security.py   hashing, tokens, rate limiting
app/notify.py     iPhone push via ntfy
app/templates/    pages (profile, wait, private, admin, login)
manage.py         init / password / rotate-band / url / taps
tests/            end-to-end flow tests
```

## Next phases

3. iPhone automation  4. ESP32 + PN532 desk reader  5. PC launcher  6. AI daily brief  7. Vault  8. Unified command dashboard
