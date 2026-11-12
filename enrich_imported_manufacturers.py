from __future__ import annotations

import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "India Medicines and Drug Info Dataset.csv"
TARGET = ROOT / "updated_indian_medicine_data.csv"


def clean(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


with SOURCE.open("r", encoding="utf-8-sig", newline="") as handle:
    source = {clean(row.get("Medicine Name")): row for row in csv.DictReader(handle)}
with TARGET.open("r", encoding="utf-8-sig", newline="") as handle:
    rows = list(csv.DictReader(handle))

updated = 0
for row in rows:
    if not row.get("id", "").startswith("source-") or row.get("manufacturer_name"):
        continue
    source_row = source.get(clean(row.get("name")))
    if not source_row:
        continue
    raw = source_row.get("Type of Medicine", "")
    match = re.search(r"(?:strip|pack|bottle|box|packet)\s+of\s+\d+[^A-Za-z]*(?:tablets?|capsules?|ml|g|l)", raw, re.I)
    tail = raw[match.end():] if match else raw
    composition = source_row.get("Composition", "")
    ingredient = re.split(r"\s*\+\s*", composition)[0]
    ingredient = re.sub(r"\s*\(.*$", "", ingredient).strip()
    if ingredient:
        position = tail.lower().find(ingredient.lower())
        if position > 0:
            manufacturer = tail[:position].strip()
            if manufacturer:
                row["manufacturer_name"] = manufacturer
                updated += 1

with TARGET.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
print(f"manufacturers_added={updated}")
