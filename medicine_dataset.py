"""Local CSV medicine catalog used to enrich Gemini identification results."""
from __future__ import annotations

import csv
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from difflib import SequenceMatcher

ROOT = Path(__file__).resolve().parent
DETAIL_FILE = ROOT / "updated_indian_medicine_data.csv"
CATALOG_FILE = ROOT / "Extensive_A_Z_medicines_dataset_of_India.csv"


def _clean(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _money(value: object) -> Optional[Decimal]:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return Decimal(match.group())
    except InvalidOperation:
        return None


def parse_pack_quantity(label: object) -> tuple[Optional[int], Optional[str]]:
    """Return (count, unit) without treating liquid volume as tablet count."""
    text = re.sub(r"\s+", " ", str(label or "").lower()).strip()
    units = r"tablets?|capsules?|soft gelatin capsules?"
    match = re.search(rf"(\d+)\s*x\s*(\d+)\s*({units})", text)
    if match:
        return int(match.group(1)) * int(match.group(2)), "capsule" if "capsule" in match.group(3) else "tablet"
    match = re.search(rf"(\d+)\s+strips?\s+of\s+(\d+)\s*({units})", text)
    if match:
        return int(match.group(1)) * int(match.group(2)), "capsule" if "capsule" in match.group(3) else "tablet"
    match = re.search(rf"(?:strip|pack|box|packet)\s+of\s+(\d+)\s*({units})", text)
    if match:
        return int(match.group(1)), "capsule" if "capsule" in match.group(2) else "tablet"
    match = re.search(rf"\b(\d+)\s*({units})\b", text)
    if match:
        return int(match.group(1)), "capsule" if "capsule" in match.group(2) else "tablet"
    volume = re.search(r"(?:bottle|packet|pack)\s+of\s+(\d+(?:\.\d+)?)\s*(ml|l|g|kg)\b", text)
    if volume:
        return int(float(volume.group(1))), volume.group(2)
    return None, None


def calculate_per_unit_price(pack_price: object, pack_quantity: Optional[int], unit: Optional[str], price_is_pack: bool = True) -> Optional[float]:
    """Calculate a display-ready per-tablet/capsule price using Decimal."""
    if pack_price is None or unit not in {"tablet", "capsule"}:
        return None
    try:
        price = Decimal(str(pack_price))
        if not price_is_pack:
            return float(price.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        if pack_quantity is None or pack_quantity <= 0:
            return None
        value = (price / Decimal(pack_quantity)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return float(value)
    except (InvalidOperation, TypeError, ValueError, ZeroDivisionError):
        return None


def _composition(row: dict) -> list[str]:
    value = row.get("salt_composition") or ""
    if not value:
        value = " + ".join(x for x in (row.get("short_composition1"), row.get("short_composition2")) if x)
    return [part.strip() for part in re.split(r"\s*\+\s*", value) if part.strip()]


def _list_value(value: object) -> list[str]:
    return [part.strip() for part in re.split(r"\s*[,;]\s*", str(value or "")) if part.strip()]


def _description_sentences(description: object, keywords: tuple[str, ...]) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", str(description or ""))
    return [sentence.strip() for sentence in sentences if any(word in sentence.lower() for word in keywords)][:5]


def _interactions(value: object) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(str(value))
        values = []
        for key in ("drug", "brand"):
            values.extend(str(item).strip() for item in parsed.get(key, []) if str(item).strip())
        return values
    except (TypeError, ValueError, json.JSONDecodeError):
        return _list_value(value)


def _form(name: str) -> Optional[str]:
    text = _clean(name)
    for value in ("tablet", "capsule", "syrup", "suspension", "injection", "cream", "ointment", "drops", "gel"):
        if value in text:
            return value
    return None


def _key(composition: list[str]) -> frozenset[str]:
    values = []
    for item in composition:
        if isinstance(item, dict):
            item = f"{item.get('name', '')} {item.get('value', '')} {item.get('unit', '')}"
        value = _clean(item)
        if value:
            values.append(value)
    return frozenset(values)


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@lru_cache(maxsize=1)
def _catalog() -> tuple[list[dict], dict[str, list[int]], dict[frozenset[str], list[int]], dict[str, set[str]]]:
    detail_rows = _read(DETAIL_FILE)
    fallback_rows = _read(CATALOG_FILE)
    rows = detail_rows or fallback_rows
    by_name: dict[str, list[int]] = {}
    by_composition: dict[frozenset[str], list[int]] = {}
    by_token: dict[str, set[str]] = {}
    for index, row in enumerate(rows):
        name = _clean(row.get("name"))
        if name:
            by_name.setdefault(name, []).append(index)
            for token in name.split():
                by_token.setdefault(token, set()).add(name)
        comp = _key(_composition(row))
        if comp:
            by_composition.setdefault(comp, []).append(index)
    return rows, by_name, by_composition, by_token


def _row_product(row: dict) -> dict:
    composition = _composition(row)
    price = _money(row.get("price") or row.get("price(₹)"))
    pack = row.get("pack_size_label") or None
    quantity, pack_unit = parse_pack_quantity(pack)
    description = row.get("medicine_desc") or ""
    primary_uses = _list_value(";".join(row.get(f"use{i}") or "" for i in range(5)))
    if not primary_uses:
        primary_uses = _description_sentences(description, ("used", "treat", "relief", "fever", "infection"))
    common_side_effects = _list_value(row.get("side_effects") or row.get("Consolidated_Side_Effects"))
    precautions = _description_sentences(description, ("before", "caution", "allerg", "kidney", "liver", "pregnan", "doctor"))
    overdose = _description_sentences(description, ("overdose",))
    storage = _description_sentences(description, ("store", "storage", "temperature"))
    composition = _composition(row)
    ingredients = []
    for item in composition:
        match = re.match(r"^(.+?)\s*(?:\(|\[)\s*(\d+(?:\.\d+)?)\s*([a-zA-Zµ%]+)", item)
        ingredients.append({"name": match.group(1).strip() if match else item, "strength": match.group(2) if match else None,
                            "unit": match.group(3).lower() if match else None, "type": "active", "role": None})
    return {
        "id": row.get("id") or None,
        "name": row.get("name") or None,
        "generic_name": composition[0] if composition else None,
        "strength": None,
        "composition": composition,
        "ingredients": ingredients,
        "composition_text": " + ".join(composition) or None,
        "manufacturer": row.get("manufacturer_name") or None,
        "dosage_form": _form(row.get("name") or ""),
        "route": None,
        "packagings": [{"label": pack, "quantity": quantity, "unit": pack_unit, "mrp": float(price) if price is not None else None}] if (pack or price is not None) else [],
        "uses": "; ".join(primary_uses) or None,
        "primary_uses": primary_uses,
        "side_effects": "; ".join(common_side_effects) or None,
        "common_side_effects": common_side_effects,
        "serious_side_effects": [],
        "precautions": "; ".join(precautions) or None,
        "clinical_precautions": precautions,
        "warnings": [],
        "interactions": "; ".join(_interactions(row.get("drug_interactions"))) or None,
        "drug_interactions": _interactions(row.get("drug_interactions")),
        "overdose_information": overdose,
        "storage_instructions": storage,
        "prescription_classification": "Prescription required" if str(row.get("Is Prescription Required?")).lower() == "true" else None,
        "schedule": "Prescription required" if str(row.get("Is Prescription Required?")).lower() == "true" else None,
        "image_url": row.get("Image URL") or None,
        "data_source": "local CSV dataset",
        "verification_status": "unverified",
        "ai_generated": False,
    }


def find(names: list[str]) -> Optional[dict]:
    rows, by_name, _, by_token = _catalog()
    candidates = [_clean(name) for name in names if _clean(name)]
    if not candidates:
        return None
    best_index = None
    best_score = 0.0
    for query in candidates[:5]:
        for index in by_name.get(query, []):
            return _row_product(rows[index])
        candidate_names = set()
        for token in query.split():
            candidate_names.update(by_token.get(token, set()))
        if not candidate_names:
            candidate_names = set(by_name)
        for name in candidate_names:
            indexes = by_name[name]
            score = SequenceMatcher(None, query, name).ratio()
            if query in name or name in query:
                score = max(score, 0.86)
            if score > best_score:
                best_score, best_index = score, indexes[0]
    return _row_product(rows[best_index]) if best_index is not None and best_score >= 0.78 else None


def enrich(product: Optional[dict], names: list[str]) -> Optional[dict]:
    local = find(names or ([product.get("name")] if product and product.get("name") else []))
    if not local:
        return product
    if not product:
        return local
    merged = dict(local)
    for key, value in product.items():
        if value not in (None, "", [], {}) and key not in {"packagings", "uses", "side_effects", "interactions", "manufacturer", "composition", "composition_text"}:
            merged[key] = value
    merged["data_source"] = "Gemini + local CSV dataset"
    merged["verification_status"] = "unverified"
    return merged


def comparable(base: dict, limit: int = 10) -> list[dict]:
    rows, _, by_composition, _ = _catalog()
    comp = _key(base.get("composition") or [])
    if not comp:
        return []
    base_form = base.get("dosage_form") or _form(base.get("name") or "")
    if not base_form:
        return []
    matches = []
    base_pack = (base.get("packagings") or [{}])[0]
    scanned = dict(base)
    scanned.update({
        "pack_label": base_pack.get("label"),
        "quantity": base_pack.get("quantity"),
        "mrp": base_pack.get("mrp"),
        "per_unit": calculate_per_unit_price(base_pack.get("mrp"), base_pack.get("quantity"), base_pack.get("unit")),
        "is_scanned": True,
        "is_lowest": False,
    })
    matches.append(scanned)
    for index in by_composition.get(comp, []):
        product = _row_product(rows[index])
        if product.get("name") == base.get("name"):
            continue
        if product.get("dosage_form") != base_form:
            continue
        pack = (product.get("packagings") or [{}])[0]
        product.update({
            "pack_label": pack.get("label"),
            "quantity": pack.get("quantity"),
            "mrp": pack.get("mrp"),
            "per_unit": calculate_per_unit_price(pack.get("mrp"), pack.get("quantity"), pack.get("unit")),
            "is_scanned": False,
            "is_lowest": False,
        })
        matches.append(product)
    matches.sort(key=lambda item: item.get("mrp") is None)
    return matches[:limit]


def enrich_result(result: dict, names: list[str]) -> dict:
    match = enrich(result.get("match"), names)
    if match:
        local_rows = comparable(match)
        result["match"] = match
        result["comparison"] = {"rows": local_rows, "basis": "mrp", "price_status": "local_dataset"} if local_rows else {"rows": [], "basis": "mrp", "price_status": "unavailable"}
    return result
