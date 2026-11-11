"""DrugSetu API client for MediScan India.

======================  INTEGRATION STATUS: UNVERIFIED  ======================
The official DrugSetu API reference is behind a login (app.drugsetu.com -> Docs)
and was NOT available when this module was written, and no API key was
supplied, so NO real request has been made to DrugSetu by this project.

What is known from public sources (two sources, which disagree on the host):
  * Official DrugSetu website API example: base https://api.drugsetu.in/v1,
    GET /medicines/search?q=..., header X-API-Key with a ds_live_... key.
  * Detail endpoint and complete response schema are not shown in the public
    example; detail lookup remains optional and configurable.
  * Response field names are NOT published publicly (only `product_form`,
    `mrp`, `packagings`, composition strings like "Metformin (500mg)").

Therefore base URL, paths, auth header and the response field names are all
configurable below. Check them against YOUR DrugSetu dashboard docs and fix
`FIELD_MAP` / the env vars if needed. Anything that is not found in a response
is reported as missing and the UI shows "Information unavailable".
==============================================================================
"""
from __future__ import annotations

import os
import re
import threading
import time
from difflib import SequenceMatcher
from typing import Any, Optional

import requests

# Official DrugSetu website API example uses api.drugsetu.in and /medicines/search.
DEFAULT_BASE_URL = "https://api.drugsetu.in/v1"
DEFAULT_SEARCH_PATH = "/medicines/search"
# Detail endpoint is not shown in the public example; keep it optional/configurable.
DEFAULT_DETAIL_PATH = "/products/{id}"
DEFAULT_AUTH_HEADER = "X-API-Key"
REQUEST_TIMEOUT = (5, 15)          # (connect, read) seconds
MIN_INTERVAL = 1.1                 # seconds between requests (documented ~1 req/s)
CONFIRM_THRESHOLD = 0.80           # name similarity needed to call a match "confirmed"

# UNVERIFIED: candidate JSON keys for each normalised field, tried in order.
FIELD_MAP = {
    "id": ["id", "product_id", "_id"],
    "name": ["name", "product_name", "brand_name", "title"],
    "generic": ["generic_name", "salt_composition", "composition_name", "generic"],
    "composition": ["composition", "compositions", "salt_composition", "ingredients",
                    "active_ingredients", "short_composition"],
    "manufacturer": ["manufacturer", "manufacturer_name", "marketing_company",
                     "marketed_by", "company", "company_name"],
    "dosage_form": ["product_form", "dosage_form", "form"],
    "route": ["route", "route_of_administration", "administration_route"],
    "packagings": ["packagings", "packaging", "packs", "pack_sizes"],
    "pack_label": ["pack_size_label", "pack_size", "pack", "package", "label", "name"],
    "pack_qty": ["quantity", "qty", "pack_quantity", "units", "unit_count"],
    "mrp": ["mrp", "price", "max_retail_price"],
    "uses": ["uses", "usage", "indications", "benefits", "information", "introduction"],
    "side_effects": ["side_effects", "side_effect", "adverse_effects"],
    "precautions": ["safety_advice", "precautions", "warnings", "how_to_use"],
    "interactions": ["interactions", "drug_interactions", "interaction"],
    "images": ["images", "image", "image_url", "product_images"],
    "schedule": ["schedule", "drug_schedule", "prescription_type", "rx_type"],
}

ABSENT = None
SOLID_FORMS = ("tablet", "capsule", "caplet", "lozenge")
FORM_SYNONYMS = {"tab": "tablet", "tabs": "tablet", "cap": "capsule", "caps": "capsule",
                 "syp": "syrup", "inj": "injection"}


class DrugSetuError(Exception):
    """Base error; message is safe to show to end users."""


class DrugSetuNotConfigured(DrugSetuError):
    pass


class DrugSetuAuthError(DrugSetuError):
    pass


class DrugSetuRateLimited(DrugSetuError):
    pass


class DrugSetuUnavailable(DrugSetuError):
    pass


# ----------------------------------------------------------------------------
# normalisation helpers
# ----------------------------------------------------------------------------
def _first(d: Any, keys: list) -> Any:
    if not isinstance(d, dict):
        return ABSENT
    for k in keys:
        v = d.get(k)
        if v not in (None, "", [], {}):
            return v
    return ABSENT


def _text(v: Any) -> Optional[str]:
    """Flatten strings / lists / dicts of text into one readable string."""
    if v in (None, "", [], {}):
        return None
    if isinstance(v, str):
        return re.sub(r"<[^>]+>", " ", v).replace("&nbsp;", " ").strip() or None
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        parts = [p for p in (_text(x) for x in v) if p]
        return "; ".join(parts) or None
    if isinstance(v, dict):
        for k in ("text", "description", "content", "value", "name", "title"):
            if k in v:
                return _text(v[k])
        parts = [p for p in (_text(x) for x in v.values()) if p]
        return "; ".join(parts) or None
    return None


_ING_RE = re.compile(r"^\s*(.+?)\s*[\(\[]\s*(\d+(?:\.\d+)?)\s*([a-zA-Zµ%]+)\s*[\)\]]\s*$")
_ING_SPACE_RE = re.compile(r"^\s*(.+?)\s+(\d+(?:\.\d+)?)\s*([a-zA-Zµ%]+)\s*$")


def parse_composition(value: Any) -> list:
    """Return [{'name', 'value', 'unit'}] from strings like 'Metformin (500mg)'."""
    items: list = []
    if value in (None, "", [], {}):
        return items
    if isinstance(value, str):
        chunks = re.split(r"\s*(?:\+|,|;|\band\b)\s*", value)
    elif isinstance(value, list):
        chunks = []
        for v in value:
            if isinstance(v, dict):
                n = _first(v, ["name", "salt", "ingredient"])
                s = _first(v, ["strength", "quantity", "value"])
                chunks.append(f"{n} ({s})" if n and s else str(n or ""))
            else:
                chunks.append(str(v))
    else:
        chunks = [str(value)]
    for chunk in chunks:
        chunk = chunk.strip()
        if not chunk:
            continue
        m = _ING_RE.match(chunk) or _ING_SPACE_RE.match(chunk)
        if m:
            items.append({"name": m.group(1).strip(), "value": m.group(2), "unit": m.group(3).lower()})
        else:
            items.append({"name": chunk, "value": None, "unit": None})
    return items


def _norm_form(form: Optional[str]) -> Optional[str]:
    if not form:
        return None
    f = re.sub(r"[^a-z ]", " ", form.lower())
    words = [FORM_SYNONYMS.get(w, w) for w in f.split()]
    for base in SOLID_FORMS + ("syrup", "injection", "suspension", "cream", "ointment", "drops", "gel"):
        if base in words:
            return base
    return " ".join(words) or None


def _parse_money(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d+(?:[.,]\d+)*", str(v))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_pack(pack_obj: Any) -> dict:
    """Extract a pack label / quantity / mrp from one packaging entry."""
    label = qty = mrp = None
    if isinstance(pack_obj, dict):
        label = _text(_first(pack_obj, FIELD_MAP["pack_label"]))
        q = _first(pack_obj, FIELD_MAP["pack_qty"])
        qty = int(q) if isinstance(q, (int, float)) and q > 0 else None
        mrp = _parse_money(_first(pack_obj, FIELD_MAP["mrp"]))
    elif isinstance(pack_obj, str):
        label = pack_obj
    if qty is None and label:
        m = re.search(r"(\d+)\s*(?:x\s*\d+\s*)?(tablet|tab|capsule|cap)s?\b", label, re.I)
        if m:
            qty = int(m.group(1))
    return {"label": label, "quantity": qty, "mrp": mrp}


def normalize_product(raw: dict) -> dict:
    """Map one raw DrugSetu product into the fields the UI uses.

    Missing fields stay None - nothing is invented.
    """
    comp_raw = _first(raw, FIELD_MAP["composition"])
    composition = parse_composition(comp_raw)
    packs_raw = _first(raw, FIELD_MAP["packagings"])
    packs = []
    if isinstance(packs_raw, list):
        packs = [_parse_pack(p) for p in packs_raw]
    elif packs_raw:
        packs = [_parse_pack(packs_raw)]
    top_mrp = _parse_money(_first(raw, FIELD_MAP["mrp"]))
    if top_mrp is not None and not packs:
        packs = [{"label": None, "quantity": None, "mrp": top_mrp}]

    imgs = _first(raw, FIELD_MAP["images"])
    image_url = None
    if isinstance(imgs, str):
        image_url = imgs
    elif isinstance(imgs, list) and imgs:
        f = imgs[0]
        image_url = f if isinstance(f, str) else _first(f, ["url", "src", "image_url"])
    elif isinstance(imgs, dict):
        image_url = _first(imgs, ["url", "src", "image_url"])
    if image_url and not str(image_url).startswith("https://"):
        image_url = None  # only show https images

    return {
        "id": _text(_first(raw, FIELD_MAP["id"])),
        "name": _text(_first(raw, FIELD_MAP["name"])),
        "generic_name": _text(_first(raw, FIELD_MAP["generic"])),
        "composition": composition,
        "composition_text": " + ".join(
            f"{c['name']} ({c['value']}{c['unit']})" if c["value"] else c["name"] for c in composition
        ) or None,
        "manufacturer": _text(_first(raw, FIELD_MAP["manufacturer"])),
        "dosage_form": _text(_first(raw, FIELD_MAP["dosage_form"])),
        "route": _text(_first(raw, FIELD_MAP["route"])),
        "packagings": packs,
        "uses": _text(_first(raw, FIELD_MAP["uses"])),
        "side_effects": _text(_first(raw, FIELD_MAP["side_effects"])),
        "precautions": _text(_first(raw, FIELD_MAP["precautions"])),
        "interactions": _text(_first(raw, FIELD_MAP["interactions"])),
        "schedule": _text(_first(raw, FIELD_MAP["schedule"])),
        "image_url": image_url,
    }


def _extract_list(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("data", "results", "products", "items", "medicines"):
            v = payload.get(k)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                inner = _extract_list(v)
                if inner:
                    return inner
    return []


def _extract_object(payload: Any) -> dict:
    if isinstance(payload, dict):
        for k in ("data", "product", "result", "medicine"):
            if isinstance(payload.get(k), dict):
                return payload[k]
        return payload
    return {}


# ----------------------------------------------------------------------------
# matching and comparison
# ----------------------------------------------------------------------------
def similarity(a: str, b: str) -> float:
    a, b = re.sub(r"\s+", " ", a.lower()).strip(), re.sub(r"\s+", " ", b.lower()).strip()
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def score_match(candidate: str, product_name: Optional[str]) -> float:
    """Name similarity; also credits a candidate that is the full leading brand token(s)."""
    if not product_name:
        return 0.0
    full = similarity(candidate, product_name)
    cand_tokens = candidate.lower().split()
    prod_tokens = product_name.lower().split()
    prefix = 0.0
    if cand_tokens and prod_tokens[:len(cand_tokens)] == cand_tokens and len(candidate) >= 5:
        prefix = 0.85
    return max(full, prefix)


def _ingredient_key(p: dict) -> Optional[frozenset]:
    comp = p.get("composition") or []
    if not comp or any(c["value"] is None for c in comp):
        return None  # cannot compare strengths reliably
    return frozenset((c["name"].lower().strip(), float(c["value"]), c["unit"]) for c in comp)


def is_equivalent(base: dict, other: dict) -> bool:
    """Same active ingredients + strengths, same dosage form, compatible route."""
    kb, ko = _ingredient_key(base), _ingredient_key(other)
    if kb is None or ko is None or kb != ko:
        return False
    fb, fo = _norm_form(base.get("dosage_form")), _norm_form(other.get("dosage_form"))
    if not fb or not fo or fb != fo:
        return False
    rb, ro = base.get("route"), other.get("route")
    if rb and ro and rb.strip().lower() != ro.strip().lower():
        return False
    return True


def build_comparison(base: dict, others: list) -> dict:
    """Valid price-comparison rows built only from API data.

    Per-unit price is computed only for solid forms with a known pack quantity.
    The lowest price is flagged only when at least two comparable priced rows exist.
    """
    rows = []
    for p in [base] + others:
        if p is not base and not is_equivalent(base, p):
            continue
        if p is not base and p.get("id") and p.get("id") == base.get("id"):
            continue
        solid = _norm_form(p.get("dosage_form")) in SOLID_FORMS
        for pack in p.get("packagings") or [{"label": None, "quantity": None, "mrp": None}]:
            qty, mrp = pack.get("quantity"), pack.get("mrp")
            rows.append({
                "name": p.get("name"),
                "manufacturer": p.get("manufacturer"),
                "pack_label": pack.get("label"),
                "quantity": qty,
                "mrp": mrp,
                "per_unit": round(mrp / qty, 2) if (solid and qty and mrp) else None,
                "is_scanned": p is base,
            })
    # remove exact duplicate rows
    seen, unique = set(), []
    for r in rows:
        key = (r["name"], r["manufacturer"], r["pack_label"], r["mrp"])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    rows = unique

    comparable = [r for r in rows if r["per_unit"] is not None]
    basis = "per_unit" if len(comparable) >= 2 else "mrp"
    pool = comparable if basis == "per_unit" else [r for r in rows if r["mrp"] is not None]
    lowest = None
    if len(pool) >= 2:
        lowest = min(pool, key=lambda r: r[basis] if basis == "per_unit" else r["mrp"])
    for r in rows:
        r["is_lowest"] = lowest is r
    rows.sort(key=lambda r: (r["per_unit"] is None, r["per_unit"] or 0, r["mrp"] or 0))
    return {"rows": rows if len(rows) > 1 else [], "basis": basis}


# ----------------------------------------------------------------------------
# HTTP client
# ----------------------------------------------------------------------------
class DrugSetuClient:
    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 session: Optional[requests.Session] = None):
        self.api_key = api_key if api_key is not None else os.environ.get("DRUGSETU_API_KEY", "")
        self.base_url = (base_url or os.environ.get("DRUGSETU_API_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.search_path = os.environ.get("DRUGSETU_SEARCH_PATH", DEFAULT_SEARCH_PATH)
        self.detail_path = os.environ.get("DRUGSETU_DETAIL_PATH", DEFAULT_DETAIL_PATH)
        self.auth_header = os.environ.get("DRUGSETU_AUTH_HEADER", DEFAULT_AUTH_HEADER)
        self.search_type = os.environ.get("DRUGSETU_SEARCH_TYPE", "")   # optional `type` param
        self.session = session or requests.Session()
        self._lock = threading.Lock()
        self._last_call = 0.0

    @property
    def configured(self) -> bool:
        return bool(self.api_key and not self.api_key.startswith("your_"))

    def _get(self, path: str, params: Optional[dict] = None) -> Any:
        if not self.configured:
            raise DrugSetuNotConfigured("The medicine database is not configured yet.")
        with self._lock:                       # respect ~1 request/second
            wait = MIN_INTERVAL - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
        try:
            resp = self.session.get(self.base_url + path, params=params,
                                    headers={self.auth_header: self.api_key, "Accept": "application/json"},
                                    timeout=REQUEST_TIMEOUT)
        except requests.Timeout as exc:
            raise DrugSetuUnavailable("The medicine database took too long to respond.") from exc
        except requests.RequestException as exc:
            raise DrugSetuUnavailable("The medicine database could not be reached.") from exc
        if resp.status_code in (401, 403):
            raise DrugSetuAuthError("The medicine database rejected the API credentials.")
        if resp.status_code == 429:
            raise DrugSetuRateLimited("Too many requests to the medicine database. Please retry shortly.")
        if resp.status_code == 404:
            return None
        if resp.status_code >= 400:
            raise DrugSetuUnavailable("The medicine database returned an error.")
        try:
            return resp.json()
        except ValueError as exc:
            raise DrugSetuUnavailable("The medicine database returned an unreadable response.") from exc

    def search(self, query: str) -> list:
        params = {"q": query}
        if self.search_type:
            params["type"] = self.search_type
        payload = self._get(self.search_path, params)
        return [normalize_product(r) for r in _extract_list(payload) if isinstance(r, dict)]

    def details(self, product_id: str) -> Optional[dict]:
        payload = self._get(self.detail_path.format(id=product_id))
        obj = _extract_object(payload)
        return normalize_product(obj) if obj else None

    # -- high level -----------------------------------------------------
    def identify(self, candidate_names: list, strengths: Optional[list] = None, max_queries: int = 2) -> dict:
        """Search the OCR name candidates and decide confirmed / possible / not_found."""
        best, best_score, all_hits, seen = None, 0.0, [], set()
        for name in candidate_names[:max_queries]:
            for hit in self.search(name):
                key = hit.get("id") or hit.get("name")
                if key in seen:
                    continue
                seen.add(key)
                hit["_score"] = round(score_match(name, hit.get("name")), 3)
                all_hits.append(hit)
                if hit["_score"] > best_score:
                    best, best_score = hit, hit["_score"]
            if best_score >= CONFIRM_THRESHOLD:
                break
        all_hits.sort(key=lambda h: h["_score"], reverse=True)
        if not all_hits:
            return {"status": "not_found", "match": None, "possible": [], "comparison": {"rows": [], "basis": "mrp"}}

        confirmed = best_score >= CONFIRM_THRESHOLD
        if confirmed:
            best = self._enrich(best)
        possible = [] if confirmed else all_hits[:3]
        comparison = {"rows": [], "basis": "mrp"}
        if confirmed:
            comparison = self._compare(best, all_hits)
        return {"status": "confirmed" if confirmed else "possible",
                "match": best if confirmed else None,
                "possible": possible, "comparison": comparison}

    def _enrich(self, product: dict) -> dict:
        """Merge the detail endpoint (uses, side effects, ...) into a search hit."""
        if not product.get("id"):
            return product
        try:
            detail = self.details(product["id"])
        except DrugSetuError:
            return product
        if not detail:
            return product
        merged = dict(product)
        for k, v in detail.items():
            if v not in (None, [], "") and (merged.get(k) in (None, [], "") or k in
                                           ("uses", "side_effects", "precautions", "interactions", "packagings")):
                merged[k] = v
        return merged

    def _compare(self, base: dict, seed_hits: list) -> dict:
        pool = list(seed_hits)
        q = (base["composition"][0]["name"] if base.get("composition") else None) or base.get("generic_name")
        if q:
            try:
                pool += self.search(q)
            except DrugSetuError:
                pass
        return build_comparison(base, pool)
