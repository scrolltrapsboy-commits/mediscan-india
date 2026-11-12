import io

import cv2
import numpy as np
import pytest

import app as app_module
import gemini_client as gc
from test_image_processor import make_label, encode


class FakeClient:
    configured = True
    def __init__(self, result=None, exc=None): self.result, self.exc = result, exc
    def identify(self, names, strengths=None, **kwargs):
        self.names = names
        if self.exc: raise self.exc
        return self.result


def post(client, data, name="x.png", ctype="image/png"):
    return client.post("/api/identify", data={"image": (io.BytesIO(data), name, ctype)}, content_type="multipart/form-data")


def make(fake=None):
    return app_module.create_app(fake or FakeClient()).test_client()


def test_app_starts_and_serves_page():
    c = make()
    r = c.get("/")
    assert r.status_code == 200 and b"Identify Indian Medicines Instantly" in r.data
    assert c.get("/healthz").get_json()["status"] == "ok"
    assert "camera=(self)" in r.headers["Permissions-Policy"]


def test_static_assets_served():
    c = make()
    for p in ["css/style.css", "js/app.js", "js/camera.js", "js/loader.js", "css/tailwind.css"]:
        assert c.get("/static/" + p).status_code == 200


def test_no_file():
    r = make().post("/api/identify", data={}, content_type="multipart/form-data")
    assert r.status_code == 400 and r.get_json()["status"] == "error"


def test_invalid_file_type_even_with_image_extension():
    r = post(make(), b"%PDF-1.4 fake", "evil.png", "image/png")
    assert r.status_code == 415


def test_gif_rejected():
    r = post(make(), b"GIF89a" + b"\x00" * 100, "a.gif", "image/gif")
    assert r.status_code == 415


def test_corrupted_image_rejected_safely():
    r = post(make(), b"\x89PNG\r\n\x1a\n" + b"garbage" * 20)
    assert r.status_code == 400 and "corrupted" in r.get_json()["message"].lower()


def test_too_large():
    r = post(make(), b"\xff\xd8\xff" + b"0" * (app_module.MAX_UPLOAD_BYTES + 10), "big.jpg", "image/jpeg")
    assert r.status_code == 413


def test_blank_image_no_text():
    r = post(make(), encode(np.full((500, 700, 3), 255, np.uint8)))
    j = r.get_json()
    assert r.status_code == 200 and j["status"] in ("no_text", "blurry") and "vision" in j


def test_full_pipeline_confirmed():
    match = gc.normalize_result({"status": "identified", "medicine": {"brand_name": "PARACETAMOL 500", "generic_name": "Paracetamol", "composition": ["Paracetamol (500mg)"], "strength": "500 mg", "dosage_form": "Tablet", "manufacturer": None, "pack_size": None, "mrp": None}, "medical_information": {"uses": None, "side_effects": ["Rare rash"], "precautions": [], "interactions": []}, "identification": {"confidence": 0.9, "evidence": ["printed name"], "warnings": []}, "verification": {"status": "unverified", "sources": []}})["match"]
    fake = FakeClient({"status": "confirmed", "match": match, "possible": [], "comparison": {"rows": [], "basis": "mrp"}})
    r = post(make(fake), encode(make_label("PARACETAMOL 500")))
    j = r.get_json()
    assert j["status"] == "confirmed" and j["match"]["name"] == "PARACETAMOL 500"
    assert any("PARACETAMOL" in n.upper() for n in fake.names)
    assert j["vision"]["width"] == 1000 and j["ocr"]["confidence"] > 50


def test_api_not_configured_still_returns_ocr():
    class NC(FakeClient):
        configured = False
    r = post(make(NC(exc=gc.GeminiNotConfigured("x"))), encode(make_label("PARACETAMOL 500")))
    j = r.get_json()
    assert r.status_code == 200 and j["status"] == "not_identified" and j["ocr"]["candidate_names"]


@pytest.mark.parametrize("exc", [gc.GeminiUnavailable("Gemini could not be reached."),
                                 gc.GeminiAuthError("Gemini rejected the API credentials.")])
def test_api_errors_are_safe(exc):
    r = post(make(FakeClient(exc=exc)), encode(make_label("PARACETAMOL 500")))
    j = r.get_json()
    assert r.status_code == 200 and j["status"] == "not_identified" and "Traceback" not in j["message"]


def test_unexpected_api_failure_hides_details():
    r = post(make(FakeClient(exc=RuntimeError("secret internal path /srv/key"))), encode(make_label("PARACETAMOL 500")))
    assert r.status_code == 200 and "secret" not in r.get_data(as_text=True)


def test_uncertain_not_auto_confirmed():
    cand = gc.normalize_result({"status": "possible", "medicine": {"brand_name": "Something Else", "generic_name": None, "composition": [], "strength": None, "dosage_form": None, "manufacturer": None, "pack_size": None, "mrp": None}, "medical_information": {"uses": None, "side_effects": [], "precautions": [], "interactions": []}, "identification": {"confidence": 0.4, "evidence": [], "warnings": []}, "verification": {"status": "unverified", "sources": []}})["possible"][0]
    fake = FakeClient({"status": "possible", "match": None, "possible": [cand], "comparison": {"rows": [], "basis": "mrp"}})
    j = post(make(fake), encode(make_label("PARACETAMOL 500"))).get_json()
    assert j["status"] == "possible" and j["match"] is None
