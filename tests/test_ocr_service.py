import cv2
import numpy as np
import pytest

import image_processor as ip
import ocr_service as ocr
from test_image_processor import make_label


def test_parse_medicine_text():
    lines = [ocr.OcrLine("AUGMENTIN 625 DUO", 0.97, 90), ocr.OcrLine("Each film coated tablet contains", 0.9, 20),
             ocr.OcrLine("Amoxicillin 500 mg", 0.95, 40), ocr.OcrLine("Marketed by Glaxo Pharma Ltd", 0.9, 22),
             ocr.OcrLine("B.No. 4378291", 0.9, 18)]
    parsed = ocr.parse_medicine_text(lines)
    assert parsed["names"][0] == "AUGMENTIN 625 DUO"
    assert "500 mg" in parsed["strengths"]
    assert any("Pharma" in m for m in parsed["manufacturers"])
    assert all("B.No" not in n and "Each film" not in n for n in parsed["names"])


def test_parse_empty():
    assert ocr.parse_medicine_text([]) == {"names": [], "strengths": [], "manufacturers": []}


def test_real_ocr_reads_synthetic_label():
    """Runs the real OCR engine on a rendered label through the OpenCV pipeline."""
    pytest.importorskip("rapidocr_onnxruntime")
    p = ip.preprocess_image(make_label("PARACETAMOL 500"))
    res = ocr.extract_text(p.candidates)
    assert res.lines, "OCR returned no text"
    assert "PARACETAMOL" in res.text.upper().replace(" ", "")  or "PARACETAMOL" in res.text.upper()


def test_blank_image_yields_no_text():
    pytest.importorskip("rapidocr_onnxruntime")
    blank = np.full((600, 800, 3), 255, np.uint8)
    p = ip.preprocess_image(blank)
    assert ocr.extract_text(p.candidates).lines == []


def test_ocr_unavailable_is_reported(monkeypatch):
    monkeypatch.setattr(ocr, "_engine", None)
    def boom(): raise ocr.OcrUnavailable("x")
    monkeypatch.setattr(ocr, "_get_engine", boom)
    with pytest.raises(ocr.OcrUnavailable):
        ocr.run_ocr_on_image(np.zeros((50, 50), np.uint8))
