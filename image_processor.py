"""OpenCV image processing for MediScan India (ECE3107 Module 3 - Robot Vision).

Pipeline used by the Flask endpoint:

    bytes -> load_image() -> preprocess_image()
              |-> convert_to_grayscale()   cv2.cvtColor
              |-> reduce_noise()           cv2.blur
              |-> detect_edges()           cv2.Canny
              '-> prepare_for_ocr()        picks the best OCR input

The Canny edge map is NOT used as OCR input. It is used to (a) measure how much
structure/text-like detail the photo contains and (b) locate the packaging
region so a tighter crop can be offered to OCR as a fallback candidate.
Edge detection does not identify the medicine by itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

MAX_SIDE = 2400          # only larger images are down-scaled (memory safety)
MIN_SIDE = 1200          # smaller images are up-scaled for OCR readability
BLUR_KERNEL = (3, 3)     # small kernel: removes sensor noise, keeps text strokes
CANNY_LOW, CANNY_HIGH = 50, 150
BLUR_THRESHOLD = 60.0    # Laplacian variance below this => likely out of focus


class ImageError(ValueError):
    """Raised when an image cannot be decoded or is unusable."""


@dataclass
class OcrCandidate:
    name: str
    image: np.ndarray


@dataclass
class ProcessedImage:
    width: int                      # original resolution
    height: int
    sharpness: float                # Laplacian variance of the grayscale image
    is_blurry: bool
    edge_density: float             # fraction of pixels that are Canny edges
    scores: dict = field(default_factory=dict)
    selected: str = "grayscale"
    roi_found: bool = False
    candidates: list = field(default_factory=list)   # ordered OCR candidates


# --------------------------------------------------------------------------
# A. Image reading
# --------------------------------------------------------------------------
def load_image(data: bytes) -> np.ndarray:
    """Decode raw bytes into a BGR image using cv2.imdecode.

    Raises ImageError for empty, corrupted or undecodable data.
    """
    if not data:
        raise ImageError("The image file is empty.")
    try:
        buf = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    except cv2.error as exc:  # pragma: no cover - defensive
        raise ImageError("The image could not be read.") from exc
    if img is None or img.size == 0:
        raise ImageError("The image is corrupted or not a supported image.")
    h, w = img.shape[:2]
    if h < 32 or w < 32:
        raise ImageError("The image is too small to read.")
    return img


# --------------------------------------------------------------------------
# B / C / D. Grayscale, noise reduction, edges
# --------------------------------------------------------------------------
def convert_to_grayscale(img: np.ndarray) -> np.ndarray:
    """cv2.cvtColor: BGR -> single-channel grayscale."""
    if img is None or img.ndim != 3 or img.shape[2] != 3:
        raise ImageError("Expected a 3-channel colour image.")
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def reduce_noise(gray: np.ndarray, ksize: tuple = BLUR_KERNEL) -> np.ndarray:
    """cv2.blur: normalised box filter (mean) to suppress sensor noise."""
    if gray is None or gray.ndim != 2:
        raise ImageError("Expected a grayscale image.")
    return cv2.blur(gray, ksize)


def detect_edges(gray: np.ndarray, low: int = CANNY_LOW, high: int = CANNY_HIGH) -> np.ndarray:
    """cv2.Canny: binary edge map (0 or 255). Not used as OCR input."""
    if gray is None or gray.ndim != 2:
        raise ImageError("Expected a grayscale image.")
    return cv2.Canny(gray, low, high)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _resize_for_processing(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    longest = max(h, w)
    if longest > MAX_SIDE:
        scale = MAX_SIDE / longest
        return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    if longest < MIN_SIDE:
        scale = MIN_SIDE / longest
        return cv2.resize(img, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_CUBIC)
    return img


def _sharpness(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _noise_sigma(gray: np.ndarray) -> float:
    """Immerkaer's fast noise-level estimate."""
    kernel = np.array([[1, -2, 1], [-2, 4, -2], [1, -2, 1]], dtype=np.float64)
    h, w = gray.shape
    conv = cv2.filter2D(gray.astype(np.float64), -1, kernel)
    return float(np.sqrt(np.pi / 2.0) * np.abs(conv[1:-1, 1:-1]).sum() / (6.0 * (w - 2) * (h - 2)))


def _readability_score(gray: np.ndarray) -> float:
    """Detail (sharpness) relative to estimated noise. Higher = better for OCR."""
    sigma = _noise_sigma(gray)
    return _sharpness(gray) / (sigma * sigma + 1.0)


def _find_packaging_region(edges: np.ndarray):
    """Bounding box (x, y, w, h) of the dense edge region, or None.

    Dilate the Canny edges so neighbouring text/graphics merge into blobs, then
    take the union of the larger contours. A crop is only suggested when it is
    meaningfully smaller than the full frame.
    """
    h, w = edges.shape
    k = max(5, int(min(h, w) * 0.015) | 1)
    merged = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)), iterations=2)
    contours, _ = cv2.findContours(merged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    boxes = [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) > 0.005 * h * w]
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    ratio = ((x1 - x0) * (y1 - y0)) / float(h * w)
    if not 0.15 <= ratio <= 0.9:
        return None
    mx, my = int(0.03 * w), int(0.03 * h)
    x0, y0 = max(0, x0 - mx), max(0, y0 - my)
    x1, y1 = min(w, x1 + mx), min(h, y1 + my)
    return x0, y0, x1 - x0, y1 - y0


# --------------------------------------------------------------------------
# E. OCR preparation
# --------------------------------------------------------------------------
def prepare_for_ocr(gray: np.ndarray, blurred: np.ndarray, edges: np.ndarray):
    """Choose the most readable OCR input and build ordered fallbacks.

    Returns (candidates, scores, selected_name, roi_found). Candidates are
    grayscale / noise-reduced images - never the edge map. The original colour
    image is not modified.
    """
    scores = {
        "grayscale": _readability_score(gray),
        "noise_reduced": _readability_score(blurred),
    }
    images = {"grayscale": gray, "noise_reduced": blurred}
    ordered = sorted(scores, key=scores.get, reverse=True)
    selected = ordered[0]
    candidates = [OcrCandidate(n, images[n]) for n in ordered]

    roi = _find_packaging_region(edges)
    if roi is not None:
        x, y, w, h = roi
        candidates.append(OcrCandidate(f"{selected}_roi", images[selected][y:y + h, x:x + w]))
    return candidates, scores, selected, roi is not None


def preprocess_image(img: np.ndarray) -> ProcessedImage:
    """Run the full OpenCV preprocessing pipeline on a decoded BGR image."""
    if img is None or img.size == 0:
        raise ImageError("No image to process.")
    orig_h, orig_w = img.shape[:2]
    work = _resize_for_processing(img)

    gray = convert_to_grayscale(work)
    blurred = reduce_noise(gray)
    edges = detect_edges(blurred)
    sharp = _sharpness(gray)
    candidates, scores, selected, roi_found = prepare_for_ocr(gray, blurred, edges)

    return ProcessedImage(
        width=orig_w,
        height=orig_h,
        sharpness=round(sharp, 2),
        is_blurry=sharp < BLUR_THRESHOLD,
        edge_density=round(float(np.count_nonzero(edges)) / edges.size, 4),
        scores={k: round(v, 3) for k, v in scores.items()},
        selected=selected,
        roi_found=roi_found,
        candidates=candidates,
    )
