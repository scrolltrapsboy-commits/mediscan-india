"""OCR + medicine-text parsing for MediScan India.

Engine: RapidOCR (ONNX Runtime). It installs with plain `pip`, so no system
package (e.g. the Tesseract binary) is needed on Render.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field

MIN_LINE_CONFIDENCE = 0.5

_engine = None
_engine_lock = threading.Lock()
_inference_lock = threading.Lock()


class OcrUnavailable(RuntimeError):
    """OCR engine could not be loaded."""


@dataclass
class OcrLine:
    text: str
    confidence: float
    height: float          # text box height in pixels (proxy for font size)


@dataclass
class OcrResult:
    lines: list = field(default_factory=list)
    candidate_used: str = ""

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)

    @property
    def mean_confidence(self) -> float:
        if not self.lines:
            return 0.0
        return sum(l.confidence for l in self.lines) / len(self.lines)


def _get_engine():
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                try:
                    from rapidocr_onnxruntime import RapidOCR
                    _engine = RapidOCR()
                except Exception as exc:  # pragma: no cover
                    raise OcrUnavailable("OCR engine is not available.") from exc
    return _engine


def _clean(text: str) -> str:
    text = re.sub(r"[^\w\s%+().,/&'-]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def run_ocr_on_image(image) -> list:
    """Run the OCR engine on one image array and return OcrLine objects."""
    engine = _get_engine()
    try:
        # RapidOCR/ONNX sessions are reused and inference is serialized to avoid
        # concurrent activation buffers on the 512 MiB Render instance.
        with _inference_lock:
            raw, _ = engine(image)
    except Exception as exc:
        raise OcrUnavailable("OCR failed while reading the image.") from exc
    lines = []
    for item in raw or []:
        box, text, score = item[0], item[1], float(item[2])
        text = _clean(str(text))
        if len(text) < 2 or score < MIN_LINE_CONFIDENCE:
            continue
        ys = [p[1] for p in box]
        lines.append(OcrLine(text=text, confidence=score, height=float(max(ys) - min(ys))))
    return lines


def extract_text(candidates) -> OcrResult:
    """Try OCR candidates in order; return the first that yields readable text."""
    best = OcrResult()
    for cand in candidates:
        lines = run_ocr_on_image(cand.image)
        if lines:
            return OcrResult(lines=lines, candidate_used=cand.name)
    return best


# --------------------------------------------------------------------------
# Parsing medicine details from OCR text
# --------------------------------------------------------------------------
STRENGTH_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s?(mg|mcg|µg|g|ml|iu|%)\b", re.IGNORECASE)
MANUFACTURER_RE = re.compile(
    r"\b(pharma\w*|laborator\w*|labs?|ltd\.?|limited|healthcare|lifesciences?|"
    r"remedies|biotech|drugs?|industries|formulations?)\b", re.IGNORECASE)
BOILERPLATE_RE = re.compile(
    r"\b(b\.?\s?no|batch|mfg|mfd|exp|expiry|m\.?r\.?p|rs\.?|inclusive|taxes|"
    r"keep out|reach of children|store|storage|protect|schedule|rx|prescription|"
    r"each|contains|dosage|as directed|physician|temperature|made in|marketed|"
    r"tablets? ip|capsules? ip|film.?coated|warning|not to be sold|retail)\b",
    re.IGNORECASE)


def parse_medicine_text(lines: list) -> dict:
    """Pull possible names, strengths and manufacturers out of OCR lines."""
    text_all = " ".join(l.text for l in lines)
    strengths = []
    for m in STRENGTH_RE.finditer(text_all):
        s = f"{m.group(1)} {m.group(2).lower()}"
        if s not in strengths:
            strengths.append(s)

    manufacturers = []
    for l in lines:
        if MANUFACTURER_RE.search(l.text) and not re.search(r"\b(mfg|mfd|b\.?no)\b", l.text, re.I):
            if l.text not in manufacturers:
                manufacturers.append(l.text)

    # Brand-name candidates: prominent (large, confident) lines that are not
    # packaging boilerplate. Prominence = text height x confidence.
    scored = []
    for l in lines:
        t = l.text
        if BOILERPLATE_RE.search(t) or MANUFACTURER_RE.search(t):
            continue
        letters = sum(ch.isalpha() for ch in t)
        if letters < 3 or letters / max(len(t), 1) < 0.5:
            continue
        scored.append((l.height * l.confidence, t))
    scored.sort(reverse=True)

    names = []
    for _, t in scored:
        name = re.sub(r"\s+", " ", t).strip(" .,-")
        if name.lower() not in [n.lower() for n in names]:
            names.append(name)
        if len(names) >= 4:
            break

    return {"names": names, "strengths": strengths, "manufacturers": manufacturers[:2]}
