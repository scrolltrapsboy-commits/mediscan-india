import cv2
import numpy as np
import pytest

import image_processor as ip


def make_label(text="AUGMENTIN 625 DUO", noise=False):
    img = np.full((600, 1000, 3), 255, np.uint8)
    cv2.putText(img, text, (60, 250), cv2.FONT_HERSHEY_SIMPLEX, 2.4, (20, 20, 20), 6, cv2.LINE_AA)
    cv2.putText(img, "Amoxicillin 500 mg", (60, 400), cv2.FONT_HERSHEY_SIMPLEX, 1.6, (40, 40, 40), 4, cv2.LINE_AA)
    if noise:
        rng = np.random.default_rng(0)
        img = np.clip(img + rng.normal(0, 25, img.shape), 0, 255).astype(np.uint8)
    return img


def encode(img, ext=".png"):
    ok, buf = cv2.imencode(ext, img)
    assert ok
    return buf.tobytes()


def test_load_image_ok():
    img = ip.load_image(encode(make_label()))
    assert img.ndim == 3 and img.shape[2] == 3


@pytest.mark.parametrize("data", [b"", b"not an image at all", b"\xff\xd8\xff" + b"\x00" * 50])
def test_load_image_rejects_bad_data(data):
    with pytest.raises(ip.ImageError):
        ip.load_image(data)


def test_grayscale_conversion_uses_cvtcolor():
    img = make_label()
    g = ip.convert_to_grayscale(img)
    assert g.ndim == 2 and g.shape == img.shape[:2]
    assert np.array_equal(g, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    with pytest.raises(ip.ImageError):
        ip.convert_to_grayscale(g)


def test_blur_reduces_noise():
    g = ip.convert_to_grayscale(make_label(noise=True))
    b = ip.reduce_noise(g)
    assert b.shape == g.shape
    assert ip._noise_sigma(b) < ip._noise_sigma(g)
    assert np.array_equal(b, cv2.blur(g, ip.BLUR_KERNEL))


def test_canny_edges_binary_and_detect_text():
    g = ip.convert_to_grayscale(make_label())
    e = ip.detect_edges(g)
    assert set(np.unique(e)).issubset({0, 255})
    assert np.count_nonzero(e) > 0
    flat = ip.detect_edges(np.full((200, 200), 128, np.uint8))
    assert np.count_nonzero(flat) == 0


def test_prepare_for_ocr_never_uses_edge_map():
    img = make_label()
    g = ip.convert_to_grayscale(img); b = ip.reduce_noise(g); e = ip.detect_edges(b)
    cands, scores, selected, _ = ip.prepare_for_ocr(g, b, e)
    assert selected in ("grayscale", "noise_reduced") and set(scores) == {"grayscale", "noise_reduced"}
    for c in cands:
        assert not np.array_equal(c.image, e)
        assert len(np.unique(c.image)) > 2          # real grey levels, not a binary edge image


def test_noisy_image_prefers_noise_reduced():
    g = ip.convert_to_grayscale(make_label(noise=True)); b = ip.reduce_noise(g)
    _, scores, selected, _ = ip.prepare_for_ocr(g, b, ip.detect_edges(b))
    assert selected == "noise_reduced" and scores["noise_reduced"] > scores["grayscale"]


def test_preprocess_image_reports_original_resolution_and_blur():
    img = make_label()
    p = ip.preprocess_image(img)
    assert (p.width, p.height) == (1000, 600)
    assert not p.is_blurry
    assert p.candidates and p.edge_density > 0
    blurry = cv2.GaussianBlur(img, (0, 0), 12)
    assert ip.preprocess_image(blurry).is_blurry


def test_large_image_is_limited_small_is_upscaled():
    big = np.full((3000, 4000, 3), 200, np.uint8)
    assert max(ip._resize_for_processing(big).shape[:2]) == ip.MAX_SIDE
    small = np.full((300, 400, 3), 200, np.uint8)
    assert max(ip._resize_for_processing(small).shape[:2]) == ip.MIN_SIDE
