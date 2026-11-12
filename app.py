"""MediScan India - Flask application.

Workflow: medicine image -> OpenCV preprocessing -> OCR -> Gemini identification -> result.
Images are processed in memory only; nothing is written to disk, so there are no
temporary files to clean up.
"""
from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, render_template, request

import image_processor
import medicine_dataset
import ocr_service
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
        # Explicit diagnostic endpoint that loads the OCR engine once.
        try:
            ocr_service._get_engine()
            return jsonify(status="ok", ocr_engine="ready")
        except ocr_service.OcrUnavailable:
            log.exception("OCR readiness check failed")
            return jsonify(status="error", ocr_engine="unavailable"), 503

    @app.errorhandler(413)
    def too_large(_e):
        return jsonify(status="error", message="The image is too large. Please use a file under 10 MB."), 413

    @app.post("/api/identify")
    def identify():
        upload = request.files.get("image")
        if upload is None or not upload.filename:
            return jsonify(status="error", message="Please choose or capture a medicine photo first."), 400

        data = upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            return jsonify(status="error", message="The image is too large. Please use a file under 10 MB."), 413

        kind = sniff_image_type(data)
        if kind not in ALLOWED_TYPES:
            return jsonify(status="error",
                           message="Unsupported file. Please upload a JPG, PNG or WEBP image."), 415

        # ---- OpenCV (ECE3107 Module 3) ----
        try:
            img = image_processor.load_image(data)
            processed = image_processor.preprocess_image(img)
        except image_processor.ImageError as exc:
            return jsonify(status="error", message=str(exc)), 400
        except Exception:
            log.exception("image processing failed")
            return jsonify(status="error", message="The image could not be processed."), 500

        vision = {
            "width": processed.width, "height": processed.height,
            "sharpness": processed.sharpness, "is_blurry": processed.is_blurry,
            "edge_density": processed.edge_density, "ocr_input": processed.selected,
            "packaging_region_found": processed.roi_found,
        }

        # ---- OCR ----
        try:
            ocr = ocr_service.extract_text(processed.candidates)
        except ocr_service.OcrUnavailable:
            log.exception("OCR unavailable")
            return jsonify(status="error", message="Text recognition is temporarily unavailable.",
                           vision=vision), 503
        if not ocr.lines:
            msg = ("The photo looks blurry and no text could be read. Please retake it in good light, "
                   "holding the camera steady." if processed.is_blurry else
                   "No readable text was found. Photograph the printed name on the strip or box. "
                   "Loose tablets without markings cannot be identified from a photo.")
            return jsonify(status="blurry" if processed.is_blurry else "no_text", message=msg, vision=vision)

        parsed = ocr_service.parse_medicine_text(ocr.lines)
        ocr_info = {"text": ocr.text, "confidence": round(ocr.mean_confidence * 100, 1),
                    "candidate_names": parsed["names"], "strengths": parsed["strengths"],
                    "manufacturers": parsed["manufacturers"], "input_used": ocr.candidate_used}
        warnings = []
        if processed.is_blurry:
            warnings.append("The photo is slightly out of focus; the reading may be inaccurate.")
        if not parsed["names"]:
            return jsonify(status="no_name", vision=vision, ocr=ocr_info, warnings=warnings,
                           message="Text was found but no medicine name could be identified. "
                                   "Try a closer, sharper photo of the brand name.")

        def local_fallback(message):
            local_match = medicine_dataset.find(parsed["names"])
            if not local_match:
                return None
            return jsonify(
                status="confirmed",
                vision=vision,
                ocr=ocr_info,
                warnings=warnings + [message, "Verify this unverified CSV result against the medicine package or a pharmacist."],
                match=public_match(local_match),
                possible=[],
                comparison={"rows": medicine_dataset.comparable(local_match), "basis": "mrp", "price_status": "local_dataset"},
            )

        # ---- Gemini ----
        try:
            result = client.identify(
                parsed["names"], parsed["strengths"], image_bytes=data,
                mime_type="image/" + kind if kind != "jpg" else "image/jpeg",
                ocr_text=ocr.text, manufacturers=parsed["manufacturers"],
            )
        except GeminiNotConfigured as exc:
            fallback = local_fallback("Gemini was unavailable; the result was matched from the bundled local dataset.")
            if fallback:
                return fallback
            return jsonify(status="gemini_not_configured", vision=vision, ocr=ocr_info,
                           warnings=warnings, message=str(exc)), 503
        except GeminiAuthError as exc:
            fallback = local_fallback("Gemini credentials were rejected; the result was matched from the bundled local dataset.")
            if fallback:
                return fallback
            return jsonify(status="gemini_auth_error", vision=vision, ocr=ocr_info,
                           warnings=warnings, message=str(exc)), 502
        except GeminiModelError as exc:
            fallback = local_fallback("The Gemini model was unavailable; the result was matched from the bundled local dataset.")
            if fallback:
                return fallback
            return jsonify(status="gemini_model_error", vision=vision, ocr=ocr_info,
                           warnings=warnings, message=str(exc)), 502
        except GeminiQuotaError as exc:
            fallback = local_fallback("Gemini quota was unavailable; the result was matched from the bundled local dataset.")
            if fallback:
                return fallback
            return jsonify(status="gemini_quota_exhausted", vision=vision, ocr=ocr_info,
                           warnings=warnings, message=str(exc)), 429
        except GeminiSafetyBlocked as exc:
            fallback = local_fallback("Gemini could not analyze the image; the result was matched from the bundled local dataset.")
            if fallback:
                return fallback
            return jsonify(status="gemini_safety_blocked", vision=vision, ocr=ocr_info,
                           warnings=warnings, message=str(exc)), 422
        except GeminiInvalidResponse as exc:
            fallback = local_fallback("Gemini returned no usable result; the result was matched from the bundled local dataset.")
            if fallback:
                return fallback
            log.warning("Gemini returned invalid data: %s", exc)
            return jsonify(status="gemini_invalid_response", vision=vision, ocr=ocr_info,
                           warnings=warnings, message=str(exc)), 502
        except GeminiError as exc:
            fallback = local_fallback("Gemini was temporarily unavailable; the result was matched from the bundled local dataset.")
            if fallback:
                return fallback
            log.warning("Gemini error: %s", exc)
            return jsonify(status="gemini_unavailable", vision=vision, ocr=ocr_info,
                           warnings=warnings, message=str(exc)), 502
        except Exception:
            fallback = local_fallback("Gemini was temporarily unavailable; the result was matched from the bundled local dataset.")
            if fallback:
                return fallback
            log.exception("unexpected Gemini failure")
            return jsonify(status="gemini_unavailable", vision=vision, ocr=ocr_info, warnings=warnings,
                           message="Gemini medicine identification failed. Please try again."), 502

        # Local CSV records provide pack, manufacturer, composition and price data.
        # Gemini remains responsible for reading the image; CSV values are shown as
        # unverified dataset information and are never treated as live prices.
        result = medicine_dataset.enrich_result(result, parsed["names"])
        if result["status"] == "not_identified":
            local_match = medicine_dataset.find(parsed["names"])
            if local_match:
                result = {
                    "status": "confirmed",
                    "match": local_match,
                    "possible": [],
                    "comparison": {"rows": medicine_dataset.comparable(local_match), "basis": "mrp", "price_status": "local_dataset"},
                    "warnings": ["Matched against the bundled CSV dataset. Verify the package with a pharmacist."],
                }

        if result["status"] == "not_identified":
            return jsonify(status="not_identified", vision=vision, ocr=ocr_info,
                           warnings=warnings + ["Gemini could not identify this medicine from readable evidence."],
                           message="The medicine could not be identified. Please provide a clearer photo of the printed packaging.")
        if ocr.mean_confidence < 0.6 and result["status"] == "confirmed":
            warnings.append("Text recognition confidence was low. Please check the name against your pack.")

        return jsonify(
            status=result["status"], vision=vision, ocr=ocr_info,
            warnings=warnings + result.get("warnings", []),
            match=public_match(result["match"]) if result["match"] else None,
            possible=[public_match(p) for p in result["possible"]],
            comparison=result["comparison"],
        )

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
