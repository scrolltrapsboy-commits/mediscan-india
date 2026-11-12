import json

import pytest

import gemini_client as gc


class FakeResponse:
    def __init__(self, payload):
        self.text = json.dumps(payload)


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response, self.error, self.calls = response, error, []

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return FakeResponse(self.response)


class FakeSdk:
    def __init__(self, models):
        self.models = models


def payload(status="identified"):
    return {
        "status": status,
        "medicine": {"brand_name": "Paracetamol 500", "generic_name": "Paracetamol", "composition": ["Paracetamol (500mg)"], "strength": "500 mg", "dosage_form": "Tablet", "manufacturer": None, "pack_size": None, "mrp": None},
        "medical_information": {"uses": None, "side_effects": [], "precautions": [], "interactions": []},
        "comparison": {"alternatives": []},
        "identification": {"confidence": 0.91, "evidence": ["printed brand name"], "warnings": []},
        "verification": {"status": "unverified", "sources": []},
    }


def test_missing_key_makes_no_sdk_call():
    client = gc.GeminiMedicineClient(api_key="")
    with pytest.raises(gc.GeminiNotConfigured):
        client.identify(["Paracetamol"], image_bytes=b"image")


def test_valid_structured_response_is_normalized():
    models = FakeModels(payload())
    client = gc.GeminiMedicineClient(api_key="test", sdk_client=FakeSdk(models))
    result = client.identify(["Paracetamol"], ["500 mg"], image_bytes=b"image", mime_type="image/png")
    assert result["status"] == "confirmed"
    assert result["match"]["verification_status"] == "unverified"
    assert result["match"]["composition"][0]["value"] == "500"
    assert models.calls[0]["model"] == "gemini-3.5-flash-lite"


def test_detailed_information_and_combination_ingredients_are_preserved():
    value = payload()
    value["medicine"]["composition"] = ["Paracetamol (500mg)", "Caffeine (30mg)"]
    value["medicine"]["ingredients"] = [
        {"name": "Paracetamol", "strength": "500", "unit": "mg", "type": "active", "role": None},
        {"name": "Caffeine", "strength": "30", "unit": "mg", "type": "active", "role": None},
    ]
    value["medical_information"].update({
        "primary_uses": ["Relief of pain"],
        "common_side_effects": ["Nausea"],
        "serious_side_effects": ["Severe allergic reaction"],
        "clinical_precautions": ["Check for duplicate ingredients"],
        "warnings": ["Do not exceed the labelled dose"],
        "drug_interactions": ["Warfarin"],
        "overdose_information": ["Seek urgent medical help"],
        "storage_instructions": ["Store below 25 C"],
        "prescription_classification": "Not verified",
    })
    result = gc.normalize_result(value)
    product = result["match"]
    assert len(product["ingredients"]) == 2
    assert product["ingredients"][1]["name"] == "Caffeine"
    assert product["serious_side_effects"] == ["Severe allergic reaction"]
    assert product["overdose_information"] == ["Seek urgent medical help"]


def test_possible_and_not_identified_are_not_confirmed():
    for status, expected in (("possible", "possible"), ("not_identified", "not_identified")):
        models = FakeModels(payload(status))
        result = gc.GeminiMedicineClient(api_key="test", sdk_client=FakeSdk(models)).identify([], image_bytes=b"image")
        assert result["status"] == expected
        if expected != "possible":
            assert result["match"] is None


def test_invalid_response_rejected():
    models = FakeModels({"status": "identified"})
    with pytest.raises(gc.GeminiInvalidResponse):
        gc.GeminiMedicineClient(api_key="test", sdk_client=FakeSdk(models)).identify([], image_bytes=b"image")


@pytest.mark.parametrize("message, error", [("429 quota exceeded", gc.GeminiQuotaError), ("401 invalid api key", gc.GeminiAuthError), ("timeout", gc.GeminiUnavailable)])
def test_provider_errors_are_mapped(message, error):
    models = FakeModels(error=RuntimeError(message))
    with pytest.raises(error):
        gc.GeminiMedicineClient(api_key="test", sdk_client=FakeSdk(models)).identify([], image_bytes=b"image")


def test_mrp_is_never_returned_from_gemini():
    result = gc.normalize_result(payload())
    assert result["comparison"]["rows"] == []
    assert result["match"]["packagings"] == []


def test_comparison_has_products_but_no_prices():
    value = payload()
    value["comparison"]["alternatives"] = [{
        "brand_name": "Example Alternative", "generic_name": "Paracetamol",
        "composition": ["Paracetamol (500mg)"], "strength": "500 mg",
        "dosage_form": "Tablet", "manufacturer": "Example Maker",
        "pack_size": "10 tablets", "evidence": "Same printed active ingredient and strength.",
    }]
    row = gc.normalize_result(value)["comparison"]["rows"][0]
    assert row["name"] == "Example Alternative"
    assert row["mrp"] is None and row["per_unit"] is None and not row["is_lowest"]