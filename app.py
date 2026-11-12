"""MediScan India - Flask application.

Workflow: medicine image -> OpenCV preprocessing -> OCR -> Gemini identification -> result.
Images are processed in memory only; nothing is written to disk, so there are no
temporary files to clean up.
"""
from __future__ import annotations

import logging
import os
import io

# Limit native numerical-library thread pools on small Render instances. These
# must be set before OpenCV / NumPy / ONNX Runtime are imported.
for _thread_var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
                    "NUMEXPR_NUM_THREADS", "ORT_INTRA_OP_NUM_THREADS",
                    "ORT_INTER_OP_NUM_THREADS"):
    os.environ.setdefault(_thread_var, "1")

from flask import Flask, jsonify, render_template, request

from gemini_client import (GeminiAuthError, GeminiError, GeminiInvalidResponse,
                           GeminiMedicineClient, GeminiModelError, GeminiNotConfigured,
                           GeminiQuotaError, GeminiSafetyBlocked)

try:  # optional: load a local .env during development
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_TYPES = {"jpg", "jpeg", "png", "webp"}

log = logging.getLogger("mediscan")


def sniff_image_type(data: bytes):
    """Identify the real file type from magic bytes (not the file extension)."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def public_match(p: dict) -> dict:
    """Fields the browser needs; internal scoring keys are dropped."""
    return {k: v for k, v in p.items() if not k.startswith("_")}


def create_app(gemini_client=None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 1024 * 64
    client = gemini_client or GeminiMedicineClient()

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        # camera is needed by this origin only
        resp.headers["Permissions-Policy"] = "camera=(self), microphone=()"
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.get("/healthz")
    def healthz():
        # Lightweight deployment health check: do not load OCR models here.
        try:
            from importlib.util import find_spec
            ocr_installed = find_spec("rapidocr_onnxruntime") is not None
        except (ImportError, ModuleNotFoundError, ValueError):
            ocr_installed = False
        return jsonify(
            status="ok",
            gemini_configured=client.configured,
            gemini_model=getattr(client, "model", None),
            ocr_package_installed=ocr_installed,
        )

    @app.get("/ocr-healthz")
    def ocr_healthz():
        # OCR is intentionally not loaded on the 512 MiB production instance.
        return jsonify(status="disabled", ocr_engine="not_used",
                       message="Medicine package text is analyzed directly from the image by Gemini.")

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify(status="error", message="The image is too large. Please use a file under 10 MB."), 413

    @app.errorhandler(Exception)
    def unexpected_error(exc):
        # Keep API failures machine-readable; otherwise Flask's default HTML
        # error page makes the browser report "unreadable response".
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return jsonify(status="error", message=exc.description), exc.code
        log.exception("Unhandled application error")
        if request.path.startswith("/api/"):
            return jsonify(status="error", message="The scan failed on the server. Check Render logs for the underlying error."), 500
        return jsonify(status="error", message="The page could not be served."), 500

    @app.post("/api/identify")
    def identify():
        """Identify directly from the uploaded package image.

        The hosted free instance has only 512 MiB RAM. Loading OpenCV,
        RapidOCR/ONNX and the in-memory 348k-row CSV index in one request can
        exceed that limit and cause Render to return a proxy-level 502 with no
        Flask traceback. Gemini already accepts the original image, so keep
        this route image-native and avoid those heavyweight local stages.
        """
        upload = request.files.get("image")
        if upload is None or not upload.filename:
            return jsonify(status="error", message="Please choose or capture a medicine photo first."), 400

        data = upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return jsonify(status="error", message="The image is too large. Please use a file under 10 MB."), 413

        kind = sniff_image_type(data)
        if kind not in ALLOWED_TYPES:
            return jsonify(status="error", message="Unsupported file. Please upload a JPG, PNG or WEBP image."), 415

        # Validate dimensions without allocating OpenCV/NumPy image buffers.
        try:
            from PIL import Image
            with Image.open(io.BytesIO(data)) as image:
                width, height = image.size
                image.verify()
            if width < 32 or height < 32:
                return jsonify(status="error", message="The image is too small to read."), 400
        except Exception:
            return jsonify(status="error", message="The image is corrupted or could not be opened."), 400

        vision = {
            "width": width,
            "height": height,
            "is_blurry": False,
            "sharpness": None,
            "edge_density": None,
            "ocr_input": "original_image",
            "packaging_region_found": False,
        }
        # The image itself is the primary evidence. No OCR-derived name is
        # fabricated; Gemini reads the visible package text directly.
        ocr_info = {
            "text": "",
            "confidence": 0,
            "candidate_names": [],
            "strengths": [],
            "manufacturers": [],
            "input_used": "Gemini image analysis",
        }

        try:
            result = client.identify(
                [], [], image_bytes=data,
                mime_type="image/jpeg" if kind == "jpeg" else "image/" + kind,
                ocr_text="",
                manufacturers=[],
            )
        except GeminiNotConfigured as exc:
            return jsonify(status="error", message=str(exc), vision=vision, ocr=ocr_info), 503
        except GeminiAuthError as exc:
            return jsonify(status="error", message=str(exc), vision=vision, ocr=ocr_info), 503
        except GeminiModelError as exc:
            return jsonify(status="error", message=str(exc), vision=vision, ocr=ocr_info), 503
        except GeminiQuotaError as exc:
            return jsonify(status="error", message=str(exc), vision=vision, ocr=ocr_info), 429
        except GeminiSafetyBlocked as exc:
            return jsonify(status="not_identified", message=str(exc), vision=vision, ocr=ocr_info,
                           match=None, possible=[], comparison={"rows": [], "basis": "unavailable", "price_status": "unavailable"}), 200
        except GeminiInvalidResponse as exc:
            log.warning("Gemini returned invalid data: %s", exc)
            return jsonify(status="error", message="The medicine image could not be interpreted. Please try a clearer package photo.",
                           vision=vision, ocr=ocr_info), 502
        except GeminiError as exc:
            log.warning("Gemini identification failed: %s", exc)
            return jsonify(status="error", message=str(exc), vision=vision, ocr=ocr_info), 503
        except Exception:
            log.exception("unexpected medicine identification failure")
            return jsonify(status="error", message="Medicine identification is temporarily unavailable. Please try again.",
                           vision=vision, ocr=ocr_info), 503

        match = result.get("match")
        possible = result.get("possible") or []
        warnings = list(result.get("warnings") or [])
        warnings.append("AI identification is unverified. Confirm the medicine name and composition on the package or with a pharmacist.")
        comparison = result.get("comparison") or {"rows": [], "basis": "unavailable", "price_status": "unavailable"}
        return jsonify(
            status=result.get("status", "not_identified"),
            vision=vision,
            ocr=ocr_info,
            warnings=warnings,
            match=public_match(match) if match else None,
            possible=[public_match(p) for p in possible],
            comparison=comparison,
            message="The medicine could not be identified from the visible package text." if result.get("status") == "not_identified" else None,
        )

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
