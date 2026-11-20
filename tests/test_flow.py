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
