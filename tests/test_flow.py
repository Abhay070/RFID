"""End-to-end test: tap -> request -> owner approves -> guest session -> revoke -> panic."""
import os
import tempfile

tmp = tempfile.mkdtemp()
os.environ["GHOST_DB"] = os.path.join(tmp, "test.db")
os.environ["GHOST_BASE_URL"] = "http://testserver"
os.environ["NTFY_TOPIC"] = ""

from fastapi.testclient import TestClient  # noqa: E402

from app import db, security  # noqa: E402
from app.main import app  # noqa: E402

BAND = "testband1234567x"
PW = "correct-horse-battery"


def setup_module():
    with db.tx() as c:
        c.execute("INSERT INTO owner (id, name, pw_hash, band_id) VALUES (1, 'Abhay Bailey', ?, ?)",
                  (security.hash_password(PW), BAND))
        c.execute("INSERT INTO resources (title, url, note, created) VALUES ('CV', 'https://example.com/cv', '', 0)")
        c.execute("INSERT INTO resources (title, url, note, created) VALUES ('Secret', 'https://example.com/s', '', 0)")


def owner_client():
    o = TestClient(app)
    r = o.post("/admin/login", data={"password": PW}, follow_redirects=False)
    assert r.status_code == 303
    return o


def test_unknown_band_is_404():
    assert TestClient(app).get("/t/wrongband").status_code == 404


def test_full_flow():
    visitor, owner = TestClient(app), owner_client()

    r = visitor.get(f"/t/{BAND}")
    assert r.status_code == 200 and "Abhay Bailey" in r.text and "Request access" in r.text
    assert visitor.get(f"/t/{BAND}/contact.vcf").text.startswith("BEGIN:VCARD")

    r = visitor.post(f"/t/{BAND}/request", data={"name": "Riya", "reason": "Saw your demo"}, follow_redirects=False)
    req_id = r.headers["location"].split("/")[-1]
    assert visitor.get(f"/api/requests/{req_id}").json()["status"] == "pending"
    assert TestClient(app).get(f"/api/requests/{req_id}").status_code == 403  # other browser can't claim it
    assert visitor.get("/private").status_code == 403

    assert "Riya" in owner.get("/admin").text
    owner.post(f"/admin/requests/{req_id}/approve", data={"minutes": "15", "resource": ["1"]})

    j = visitor.get(f"/api/requests/{req_id}").json()
    assert j["status"] == "approved"
    page = visitor.get("/private")
    assert page.status_code == 200 and "CV" in page.text and "Secret" not in page.text
    assert visitor.get("/r/1", follow_redirects=False).headers["location"] == "https://example.com/cv"
    assert visitor.get("/r/2", follow_redirects=False).status_code == 403  # not shared

    # Approval can't be claimed twice
    assert visitor.get(f"/api/requests/{req_id}").json()["status"] != "approved"

    # Revoke from the dashboard
    with db.tx() as c:
        sid = c.execute("SELECT id FROM sessions WHERE kind='guest'").fetchone()["id"]
    owner.post(f"/admin/sessions/{sid}/revoke")
    assert visitor.get("/private").status_code == 403


def test_panic_and_audit():
    v, owner = TestClient(app), owner_client()
    r = v.post(f"/t/{BAND}/request", data={"name": "Sam"}, follow_redirects=False)
    req_id = r.headers["location"].split("/")[-1]
    owner.post(f"/admin/requests/{req_id}/approve", data={"minutes": "60"})
    owner.post("/admin/panic")
    assert v.get(f"/api/requests/{req_id}").json()["status"] == "denied"
    text = owner.get("/admin").text
    assert "PANIC" in text and "access_approved" in text


def test_admin_requires_login_and_bad_password():
    c = TestClient(app)
    assert c.get("/admin", follow_redirects=False).status_code == 303
    assert c.post("/admin/login", data={"password": "nope"}).status_code == 401


def test_owner_tap_shows_banner():
    assert "Owner tap detected" in owner_client().get(f"/t/{BAND}").text


def test_cross_site_post_blocked():
    c = owner_client()
    r = c.post("/admin/panic", headers={"origin": "https://evil.example"})
    assert r.status_code == 403
