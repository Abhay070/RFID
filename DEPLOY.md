# Putting GHOST ID online (so you can write the band)

The band only holds a **URL**. That URL has to point at a running copy of this
app on the public internet (https). GitHub only *stores* the code; it does not
*run* it. This guide gets it running. Pick one path.

Your repo is already configured so there is almost nothing to type: the app
works out its own public URL, seeds itself on first boot, and lets you pair your
phone from a link. Your band ID is pre-set to **`secBG37ZaKy2F56w`**, so your
final band URL will be:

```
https://<your-app-address>/t/secBG37ZaKy2F56w
```

---

## Option A — Render (easiest, free)

Free, no card needed. The catch: a free instance sleeps after ~15 min idle and
its disk resets on wake, so visitor recon, tap history and paired devices are
cleared periodically. Your **band URL never breaks** and you can always re-pair
with the setup link. For a permanent, always-on database, see "Make it
persistent" below (~$7/mo).

1. Go to **https://render.com** and sign up with your GitHub account.
2. Click **New → Blueprint**. Pick your **`rfid`** repo. Render reads
   `render.yaml` and fills everything in. Click **Apply** / **Create**.
3. Wait for the build to finish (a few minutes). Render shows your live URL,
   like `https://ghost-id.onrender.com`.
4. Open the service → **Environment** tab. Copy the generated value of
   **`GHOST_SETUP_KEY`** (and `NTFY_TOPIC` if you want push alerts).
5. On your **iPhone, in Safari**, open:
   `https://<your-url>/setup/<GHOST_SETUP_KEY>` → tap **Pair**. You land in
   Command. (Do this once. Treat that link like a password.)
6. Your band URL is `https://<your-url>/t/secBG37ZaKy2F56w`. Go write it to the
   band (see "Write the band" below).

**Make it persistent (optional, ~$7/mo):** in `render.yaml` change `plan: free`
to `plan: starter` and uncomment the `disk:` block, then push. Now nothing
resets.

---

## Option B — Fly.io (free *and* persistent, a bit more setup)

Free allowance includes a small always-on VM **and** a persistent volume, so
nothing resets. Needs their command-line tool and a card for verification (not
charged on the free allowance).

1. Install flyctl: https://fly.io/docs/flyctl/install/ and run `fly auth signup`.
2. In the project folder run: `fly launch --copy-config --name ghost-id-abhay`
   (choose your own unique name). Say **yes** to the persistent volume prompt,
   **no** to a database.
3. Set the two secrets:
   ```bash
   fly secrets set GHOST_SETUP_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(24))")
   fly secrets set NTFY_TOPIC=$(python -c "import secrets;print(secrets.token_urlsafe(16))")
   ```
   Read them back with `fly secrets list` is not possible (they're hidden), so
   note the setup key when you set it, or set it to a value you choose.
4. `fly deploy`. Your URL is `https://ghost-id-abhay.fly.dev`.
5. Pair your phone: open `https://<your-url>/setup/<your setup key>` in Safari.
6. Band URL: `https://<your-url>/t/secBG37ZaKy2F56w`.

---

## Option C — Quick test right now (temporary)

Just to see it work on your own phone today, no hosting account:

```bash
uvicorn app.main:app &                       # runs locally
cloudflared tunnel --url http://localhost:8000
```
Cloudflare prints a temporary `https://....trycloudflare.com` URL. Open
`/setup/...`? There's no setup key locally, so instead run `python manage.py pair`
in another terminal and open that link. **Don't write this URL to the band** —
it changes every restart. Use it only to prove the concept.

---

## Write the band (iPhone)

1. Install **NFC Tools** (by wakdev) from the App Store.
2. **Write → Add a record → URL/URI**, paste your band URL, **OK**.
3. Tap **Write**, hold the band to the top edge of your iPhone until it confirms.
4. Test: lock the phone, tap the band, open the banner. On your paired phone it
   opens Command; on anyone else's it shows your public profile.
5. Optional: **Other → Set password** to stop casual overwrites.
   **Never "Lock tag"** — that is permanent.

## Push alerts (optional)

Install **ntfy** from the App Store and subscribe to the exact `NTFY_TOPIC`
value from your host's dashboard. You then get a push whenever a device pairs.

## Housekeeping

- Rotate the setup key after pairing if you like: change `GHOST_SETUP_KEY` in the
  host's env. Your already-paired phone keeps working.
- `GHOST_RECON=0` turns the visitor recon demo off; `GHOST_GEO=0` turns off IP
  geolocation. On a host with a shell, `python manage.py wipe-recon` clears
  captured data after an event.
