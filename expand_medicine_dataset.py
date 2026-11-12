from __future__ import annotations

import csv
import re
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "India Medicines and Drug Info Dataset.csv"
TARGET = ROOT / "updated_indian_medicine_data.csv"
BACKUP = ROOT / f"updated_indian_medicine_data.backup-{datetime.now():%Y%m%d-%H%M%S}.csv"


def clean(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def price(value: object) -> str:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return match.group() if match else ""


def pack(value: object) -> str:
    text = str(value or "")
    match = re.search(r"((?:\d+\s*x\s*)?\d+\s*(?:tablets?|capsules?|soft gelatin capsules?|ml|g|l)\b)", text, re.I)
    return f"strip of {match.group(1)}" if match else ""


def read(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


shutil.copy2(TARGET, BACKUP)
target_rows = read(TARGET)
source_rows = read(SOURCE)
existing = {clean(row.get("name")) for row in target_rows}
added = []
for row in source_rows:
    name = row.get("Medicine Name", "").strip()
    key = clean(name)
    if not key or key in existing:
        continue
    raw_type = row.get("Type of Medicine", "")
    composition = row.get("Composition", "").strip()
    added.append({
        "id": f"source-{len(target_rows) + len(added) + 1}",
        "name": name,
        "price": price(row.get("Price")),
        "Is_discontinued": "FALSE",
        "manufacturer_name": "",
        "type": "allopathy" if "allopathy" in raw_type.lower() else "",
        "pack_size_label": pack(raw_type),
        "short_composition1": composition,
        "short_composition2": "",
        "salt_composition": composition,
        "medicine_desc": "",
        "side_effects": "",
        "drug_interactions": "",
    })
    existing.add(key)

fields = list(target_rows[0].keys())
with TARGET.open("w", encoding="utf-8", newline="") as handle:
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    writer.writerows(target_rows)
    writer.writerows(added)

print(f"backup={BACKUP.name}")
print(f"original_records={len(target_rows)}")
print(f"new_records={len(added)}")
print(f"final_records={len(target_rows) + len(added)}")
print(f"duplicate_rows_prevented={len(source_rows) - len(added) - sum(1 for row in source_rows if not clean(row.get('Medicine Name')))}")
