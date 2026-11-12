import pytest

import medicine_dataset as dataset


@pytest.mark.parametrize("price, quantity, expected", [
    ("18.00", 10, 1.80), ("174.45", 10, 17.45), ("174.45", 15, 11.63),
    ("150.00", 10, 15.00), ("168.75", 10, 16.88), ("277.10", 15, 18.47),
    ("120.00", 20, 6.00),
])
def test_per_unit_price_examples(price, quantity, expected):
    assert dataset.calculate_per_unit_price(price, quantity, "tablet") == expected


@pytest.mark.parametrize("label, expected", [
    ("1 x 10 tablets", (10, "tablet")),
    ("2 x 10 tablets", (20, "tablet")),
    ("3 strips of 10 tablets", (30, "tablet")),
    ("Strip of 15 tablets", (15, "tablet")),
    ("Pack of 20 tablets", (20, "tablet")),
    ("Strip of 10 capsules", (10, "capsule")),
])
def test_pack_quantity_parser(label, expected):
    assert dataset.parse_pack_quantity(label) == expected


def test_missing_or_non_solid_pack_is_unavailable():
    assert dataset.calculate_per_unit_price("174.45", None, "tablet") is None
    assert dataset.calculate_per_unit_price("174.45", 10, "ml") is None
    assert dataset.calculate_per_unit_price("1.80", 10, "tablet") == 0.18
    assert dataset.calculate_per_unit_price("1.80", None, "tablet", price_is_pack=False) == 1.80


def test_known_medicine_has_details_and_price():
    product = dataset.find(["Augmentin 625 Duo Tablet"])
    assert product is not None
    assert product["manufacturer"]
    assert product["composition"]
    pack = product["packagings"][0]
    assert pack["label"] == "strip of 10 tablets"
    assert pack["quantity"] == 10
    assert pack["mrp"] == 223.42
    assert product["ingredients"]
    assert product["primary_uses"]
    assert product["common_side_effects"]
    assert isinstance(product["clinical_precautions"], list)


def test_exact_174_45_record_calculates_per_tablet_price():
    product = dataset.find(["Uva Cef 200mg Tablet"])
    assert product is not None
    rows = dataset.comparable(product)
    scanned = next(row for row in rows if row["is_scanned"])
    assert scanned["mrp"] == 174.45
    assert scanned["quantity"] == 10
    assert scanned["per_unit"] == 17.45


def test_comparables_have_dataset_prices_and_no_lowest_claim():
    product = dataset.find(["Augmentin 625 Duo Tablet"])
    rows = dataset.comparable(product)
    assert rows and rows[0]["is_scanned"]
    assert rows[0]["per_unit"] == 22.34
    assert all(row["is_lowest"] is False for row in rows)


def test_unknown_medicine_stays_unavailable():
    assert dataset.find(["Medicine That Does Not Exist 999"]) is None


def test_comparison_excludes_same_ingredients_with_different_form():
    product = dataset.find(["Augmentin 625 Duo Tablet"])
    rows = dataset.comparable(product)
    assert rows
    assert all("syrup" not in (row.get("name") or "").lower() for row in rows)
    assert all("suspension" not in (row.get("name") or "").lower() for row in rows)
