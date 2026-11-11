import pytest
import requests

import drugsetu_client as ds


class FakeResp:
    def __init__(self, status=200, payload=None, bad_json=False):
        self.status_code, self._p, self._bad = status, payload, bad_json
    def json(self):
        if self._bad: raise ValueError("bad")
        return self._p


class FakeSession:
    def __init__(self, responses): self.responses, self.calls = list(responses), []
    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append((url, params, headers, timeout))
        r = self.responses.pop(0)
        if isinstance(r, Exception): raise r
        return r


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(ds.time, "sleep", lambda s: None)


def client(responses):
    s = FakeSession(responses)
    return ds.DrugSetuClient(api_key="ds_test", base_url="https://example.test/v1", session=s), s


def prod(id, name, comp, form="Tablet", mrp=100.0, qty=10, maker="Maker"):
    return {"id": id, "name": name, "composition": comp, "product_form": form, "marketing_company": maker,
            "packagings": [{"label": f"strip of {qty} tablets", "mrp": mrp}]}


def test_not_configured_makes_no_request():
    c = ds.DrugSetuClient(api_key="", session=FakeSession([]))
    with pytest.raises(ds.DrugSetuNotConfigured):
        c.search("x")
    assert c.session.calls == []


def test_placeholder_key_counts_as_unconfigured():
    assert not ds.DrugSetuClient(api_key="your_api_key_here").configured


def test_request_shape_auth_header_params_timeout():
    c, s = client([FakeResp(200, {"data": []})])
    c.search("Augmentin")
    url, params, headers, timeout = s.calls[0]
    assert url == "https://example.test/v1/products/search" and params == {"q": "Augmentin"}
    assert headers["X-API-Key"] == "ds_test" and timeout == ds.REQUEST_TIMEOUT


@pytest.mark.parametrize("resp,exc", [
    (FakeResp(401), ds.DrugSetuAuthError), (FakeResp(403), ds.DrugSetuAuthError),
    (FakeResp(429), ds.DrugSetuRateLimited), (FakeResp(500), ds.DrugSetuUnavailable),
    (FakeResp(200, bad_json=True), ds.DrugSetuUnavailable),
    (requests.Timeout(), ds.DrugSetuUnavailable), (requests.ConnectionError(), ds.DrugSetuUnavailable),
])
def test_errors_map_to_safe_exceptions(resp, exc):
    c, _ = client([resp])
    with pytest.raises(exc) as e:
        c.search("x")
    assert "ds_test" not in str(e.value)       # key never leaks into messages


def test_normalize_keeps_missing_fields_none():
    n = ds.normalize_product({"id": "1", "name": "Foo"})
    assert n["name"] == "Foo" and n["uses"] is None and n["manufacturer"] is None and n["packagings"] == []


def test_parse_composition():
    c = ds.parse_composition("Amoxicillin (500mg) + Clavulanic Acid (125mg)")
    assert [(x["name"], x["value"], x["unit"]) for x in c] == [("Amoxicillin", "500", "mg"), ("Clavulanic Acid", "125", "mg")]


def test_identify_confirmed_and_comparison_rules():
    base = prod("1", "Augmentin 625 Duo Tablet", "Amoxicillin (500mg) + Clavulanic Acid (125mg)", mrp=200, maker="GSK")
    cheap = prod("2", "Generic CV 625", "Amoxicillin (500mg) + Clavulanic Acid (125mg)", mrp=50, maker="Gov")
    other_strength = prod("3", "Other 375", "Amoxicillin (250mg) + Clavulanic Acid (125mg)", mrp=10)
    other_form = prod("4", "Syrup CV", "Amoxicillin (500mg) + Clavulanic Acid (125mg)", form="Syrup", mrp=5)
    c, s = client([FakeResp(200, {"data": [base, cheap, other_strength, other_form]}),   # search
                   FakeResp(200, {"data": dict(base, side_effects="Nausea", uses="Infections")}),  # details
                   FakeResp(200, {"data": [base, cheap, other_strength, other_form]})])  # compare search
    r = c.identify(["Augmentin 625 Duo"])
    assert r["status"] == "confirmed" and r["match"]["side_effects"] == "Nausea"
    names = [row["name"] for row in r["comparison"]["rows"]]
    assert names == ["Generic CV 625", "Augmentin 625 Duo Tablet"]      # strength/form mismatches excluded
    rows = {row["name"]: row for row in r["comparison"]["rows"]}
    assert rows["Generic CV 625"]["is_lowest"] and rows["Generic CV 625"]["per_unit"] == 5.0
    assert rows["Augmentin 625 Duo Tablet"]["is_scanned"] and not rows["Augmentin 625 Duo Tablet"]["is_lowest"]


def test_uncertain_match_is_not_confirmed():
    c, _ = client([FakeResp(200, {"data": [prod("9", "Totally Different Name", "Foo (5mg)")]})])
    r = c.identify(["Zzxq"])
    assert r["status"] == "possible" and r["match"] is None and len(r["possible"]) == 1


def test_not_found():
    c, _ = client([FakeResp(200, {"data": []})])
    assert c.identify(["Nothing"])["status"] == "not_found"


def test_no_comparison_when_single_product():
    base = prod("1", "Solo Tab", "Paracetamol (500mg)")
    cmp = ds.build_comparison(ds.normalize_product(base), [])
    assert cmp["rows"] == []


def test_no_lowest_flag_without_price_data():
    a = ds.normalize_product({"id": "1", "name": "A", "composition": "X (5mg)", "product_form": "Tablet"})
    b = ds.normalize_product({"id": "2", "name": "B", "composition": "X (5mg)", "product_form": "Tablet"})
    cmp = ds.build_comparison(a, [b])
    assert all(not r["is_lowest"] and r["per_unit"] is None for r in cmp["rows"])
