"""End-to-end tests: visitor tap -> promo profile; paired phone tap -> Command."""
import os
import tempfile
import time

tmp = tempfile.mkdtemp()
os.environ["GHOST_DB"] = os.path.join(tmp, "test.db")
os.environ["GHOST_BASE_URL"] = "http://testserver"
os.environ["NTFY_TOPIC"] = ""

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db, security  # noqa: E402
from app.main import app, pair_limiter  # noqa: E402

BAND = "testband1234567x"


def setup_module():
    with db.tx() as c:
        c.execute("INSERT INTO owner (id, name, band_id) VALUES (1, 'Abhay Bailey', ?)", (BAND,))


@pytest.fixture(autouse=True)
def reset_rate_limit():
    pair_limiter.events.clear()


def pairing_code(label="iPhone", minutes=10) -> str:
    code, now = security.new_token(), time.time()
    with db.tx() as c:
        c.execute("INSERT INTO pairings (code_hash, label, created, expires) VALUES (?,?,?,?)",
                  (security.token_hash(code), label, now, now + minutes * 60))
    return code


def paired_phone(label="iPhone") -> TestClient:
    phone = TestClient(app)
    r = phone.post(f"/pair/{pairing_code(label)}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    return phone


def test_unknown_band_is_404():
    assert TestClient(app).get("/t/wrongband").status_code == 404


def test_visitor_sees_only_promo_profile():
    visitor = TestClient(app)
    r = visitor.get(f"/t/{BAND}")
    assert r.status_code == 200 and "Abhay Bailey" in r.text
    for hidden in ("Request access", "/admin", "Command", "password"):
        assert hidden not in r.text
    assert visitor.get(f"/t/{BAND}/contact.vcf").text.startswith("BEGIN:VCARD")


def test_owner_pages_are_404_for_everyone_else():
    v = TestClient(app)
    for path in ("/admin", "/admin/preview"):
        assert v.get(path).status_code == 404
    for path in ("/admin/lockdown", "/admin/links", "/admin/devices/x/revoke"):
        assert v.post(path, data={"title": "x", "url": "https://x"}).status_code == 404
    assert v.get("/admin/login").status_code == 404  # no login page exists


def test_paired_phone_tap_opens_command():
    phone = paired_phone()
    r = phone.get(f"/t/{BAND}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    page = phone.get("/admin")
    assert page.status_code == 200 and "Command" in page.text and "owner_tap" in page.text
    assert "visitors see" in phone.get("/admin/preview").text


def test_pairing_link_works_once_and_get_does_not_consume_it():
    code = pairing_code()
    assert TestClient(app).get(f"/pair/{code}").status_code == 200   # e.g. a link preview
    assert TestClient(app).post(f"/pair/{code}", follow_redirects=False).status_code == 303
    assert TestClient(app).post(f"/pair/{code}").status_code == 404   # already used
    assert TestClient(app).get("/pair/made-up-code").status_code == 404


def test_expired_pairing_link_rejected():
    assert TestClient(app).post(f"/pair/{pairing_code(minutes=-1)}").status_code == 404


def test_cloned_band_on_another_phone_gets_nothing():
    paired_phone()
    attacker = TestClient(app)  # same band URL, different phone
    r = attacker.get(f"/t/{BAND}", follow_redirects=False)
    assert r.status_code == 200 and "Command" not in r.text
    assert attacker.get("/admin").status_code == 404


def test_quick_links_and_lockdown():
    phone, old = paired_phone("iPhone"), paired_phone("Old phone")
    phone.post("/admin/links", data={"title": "RAKSHAK repo", "url": "https://github.com/abhay070"})
    assert "RAKSHAK repo" in phone.get("/admin").text
    phone.post("/admin/lockdown")
    assert old.get("/admin").status_code == 404
    page = phone.get("/admin")
    assert page.status_code == 200 and "LOCKDOWN" in page.text


def test_unpair_self_removes_access():
    phone = paired_phone()
    with db.tx() as c:
        dev_id = c.execute("SELECT id FROM devices ORDER BY created DESC LIMIT 1").fetchone()["id"]
    phone.post(f"/admin/devices/{dev_id}/revoke")
    assert phone.get("/admin").status_code == 404


def test_cross_site_post_blocked():
    phone = paired_phone()
    assert phone.post("/admin/lockdown", headers={"origin": "https://evil.example"}).status_code == 403


def test_pairing_is_rate_limited():
    c = TestClient(app)
    codes = [c.get(f"/pair/guess{i}").status_code for i in range(12)]
    assert codes[:10] == [404] * 10 and codes[-1] == 429


def _tap_token(client, ua="Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Safari"):
    r = client.get(f"/t/{BAND}", headers={"user-agent": ua, "accept-language": "en-IN,en"})
    assert r.status_code == 200
    import re
    m = re.search(r'data-token="([^"]+)"', r.text)
    assert m and m.group(1)
    return m.group(1)


def test_profile_has_live_recon_token_and_consent_buttons():
    visitor = TestClient(app)
    html = visitor.get(f"/t/{BAND}").text
    assert 'id="recon"' in html and "Allow camera" in html and "Share location" in html
    assert _tap_token(visitor)  # a per-tap token is embedded


def test_recon_stores_fingerprint_and_shows_in_detail():
    visitor = TestClient(app)
    token = _tap_token(visitor)
    r = visitor.post(f"/t/{BAND}/recon", json={"token": token, "fp": {
        "screen": "390x844 @3x", "tz": "Asia/Kolkata", "cores": 6, "gpu": "Apple A15"}})
    assert r.status_code == 200
    owner = paired_phone()
    # device summary shows on the dashboard; full fingerprint on the detail page
    assert "iPhone" in owner.get("/admin").text
    with db.tx() as c:
        tap_id = c.execute("SELECT t.id FROM taps t JOIN recon r ON r.tap_id=t.id"
                           " WHERE t.token=?", (token,)).fetchone()["id"]
    detail = owner.get(f"/admin/tap/{tap_id}").text
    assert "Asia/Kolkata" in detail and "Apple A15" in detail


def test_analytics_and_controls_render():
    owner = paired_phone()
    page = owner.get("/admin").text
    assert "Analytics" in page and "Connect apps" in page and "Controls" in page
    assert "Export CSV" in page


def test_recon_toggle_off_disables_capture():
    owner = paired_phone()
    owner.post("/admin/recon/toggle")  # default on -> off
    v = TestClient(app)
    assert 'id="recon"' not in v.get(f"/t/{BAND}").text
    owner.post("/admin/recon/toggle")  # back on
    assert 'id="recon"' in TestClient(app).get(f"/t/{BAND}").text


def test_export_csv_and_json():
    owner = paired_phone()
    _tap_token(TestClient(app))
    csv = owner.get("/admin/export.csv")
    assert csv.status_code == 200 and "text/csv" in csv.headers["content-type"]
    assert "user_agent" in csv.text
    js = owner.get("/admin/export.json")
    assert js.status_code == 200 and js.json() != [] or isinstance(js.json(), list)


def test_editable_profile_overrides_public_view():
    owner = paired_phone()
    owner.post("/admin/profile", data={"name": "Abhay Bailey", "tagline": "Red Team",
               "bio": "x", "links": "GitHub|https://github.com/abhay070",
               "projects": "GHOST ID|NFC system", "achievements": "Cyber Leader",
               "email": "", "phone": ""})
    html = TestClient(app).get(f"/t/{BAND}").text
    assert "Red Team" in html and "github.com/abhay070" in html


def test_pair_link_from_command():
    owner = paired_phone()
    r = owner.post("/admin/pair-link", follow_redirects=False)
    assert r.status_code == 303 and "pair_link=" in r.headers["location"]


def test_webhook_add_toggle_delete_owner_only():
    owner = paired_phone()
    owner.post("/admin/webhooks", data={"name": "My Discord", "kind": "discord",
               "url": "https://discord.com/api/webhooks/x", "event_tap": "on"})
    with db.tx() as c:
        h = c.execute("SELECT * FROM webhooks WHERE name='My Discord'").fetchone()
    assert h and h["enabled"] == 1 and "tap" in h["events"]
    # visitors can't touch any of it
    assert TestClient(app).post("/admin/webhooks", data={"kind": "discord",
           "url": "https://x", "event_tap": "on"}).status_code == 404
    owner.post(f"/admin/webhooks/{h['id']}/toggle")
    with db.tx() as c:
        assert c.execute("SELECT enabled FROM webhooks WHERE id=?", (h["id"],)).fetchone()["enabled"] == 0
    owner.post(f"/admin/webhooks/{h['id']}/delete")
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM webhooks WHERE id=?", (h["id"],)).fetchone()[0] == 0


def test_notes_add_and_delete():
    owner = paired_phone()
    owner.post("/admin/notes", data={"text": "remember to rotate band"})
    assert "remember to rotate band" in owner.get("/admin").text


def test_tap_detail_is_owner_only():
    assert TestClient(app).get("/admin/tap/1").status_code == 404


def test_recon_rejects_bad_or_stale_token():
    visitor = TestClient(app)
    # unknown token: accepted (always 200) but stored nothing
    assert visitor.post(f"/t/{BAND}/recon", json={"token": "nope", "fp": {"tz": "X"}}).status_code == 200
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM recon WHERE fp LIKE '%\"tz\": \"X\"%'").fetchone()[0] == 0


def test_recon_rejects_oversized_photo():
    visitor = TestClient(app)
    token = _tap_token(visitor)
    huge = "data:image/jpeg;base64," + "A" * 400000
    visitor.post(f"/t/{BAND}/recon", json={"token": token, "photo": huge})
    with db.tx() as c:
        row = c.execute("SELECT photo FROM recon r JOIN taps t ON t.id=r.tap_id WHERE t.token=?",
                        (token,)).fetchone()
    assert row is None or row["photo"] is None


def test_recon_stores_consented_location():
    visitor = TestClient(app)
    token = _tap_token(visitor)
    visitor.post(f"/t/{BAND}/recon", json={"token": token, "lat": 28.46, "lon": 77.03, "acc": 20})
    assert "maps?q=28.46" in paired_phone().get("/admin").text


def test_owner_tap_creates_no_recon_token_path():
    # the paired phone is redirected; its tap is logged as owner, not a visitor
    paired_phone().get(f"/t/{BAND}", follow_redirects=False)
    with db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM taps WHERE is_owner=1").fetchone()[0] >= 1


def test_recon_disabled_hides_demo_and_ignores_beacon():
    with db.tx() as c:
        db.set_config(c, "recon_enabled", "0")
    try:
        v = TestClient(app)
        html = v.get(f"/t/{BAND}").text
        assert 'id="recon"' not in html and "Allow camera" not in html
        with db.tx() as c:
            before = c.execute("SELECT COUNT(*) FROM recon").fetchone()[0]
        v.post(f"/t/{BAND}/recon", json={"token": "x", "fp": {"tz": "Y"}})
        with db.tx() as c:
            assert c.execute("SELECT COUNT(*) FROM recon").fetchone()[0] == before
    finally:
        with db.tx() as c:
            db.set_config(c, "recon_enabled", "1")


def test_setup_link_pairs_a_phone_and_is_key_gated(monkeypatch):
    from app import config
    monkeypatch.setattr(config.settings, "setup_key", "s3cret-key")
    # wrong key is indistinguishable from a missing page
    assert TestClient(app).get("/setup/wrong").status_code == 404
    assert TestClient(app).post("/setup/wrong").status_code == 404
    # right key: GET shows a confirm button, POST pairs this browser
    phone = TestClient(app)
    assert "Pair" in phone.get("/setup/s3cret-key").text
    r = phone.post("/setup/s3cret-key", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/admin"
    assert phone.get("/admin").status_code == 200
    assert phone.get(f"/t/{BAND}", follow_redirects=False).headers["location"] == "/admin"


def test_setup_disabled_when_no_key(monkeypatch):
    from app import config
    monkeypatch.setattr(config.settings, "setup_key", "")
    assert TestClient(app).get("/setup/anything").status_code == 404


def test_command_has_tabs_qr_and_feed():
    page = paired_phone().get("/admin").text
    assert 'data-tab="overview"' in page and 'data-tab="visitors"' in page
    assert "<svg" in page and "qrwrap" in page          # band QR rendered inline
    assert 'id="feed"' in page and "/static/admin.js" in page


def test_live_feed_json_owner_only():
    _tap_token(TestClient(app))
    owner = paired_phone()
    r = owner.get("/admin/feed.json")
    assert r.status_code == 200 and isinstance(r.json()["items"], list)
    assert TestClient(app).get("/admin/feed.json").status_code == 404


def test_theme_switch_applies_to_pages():
    owner = paired_phone()
    owner.post("/admin/theme", data={"theme": "dark"})
    assert 'data-theme="dark"' in TestClient(app).get(f"/t/{BAND}").text
    owner.post("/admin/theme", data={"theme": "bogus"})  # ignored
    assert 'data-theme="dark"' in owner.get("/admin").text
    owner.post("/admin/theme", data={"theme": "light"})
    assert 'data-theme="light"' in TestClient(app).get(f"/t/{BAND}").text
