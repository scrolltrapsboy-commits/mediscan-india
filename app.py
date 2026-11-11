"""MediScan India - Flask application.

Workflow: medicine image -> OpenCV preprocessing -> OCR -> DrugSetu search -> result.
Images are processed in memory only; nothing is written to disk, so there are no
temporary files to clean up.
"""
from __future__ import annotations

import logging
import os

from flask import Flask, jsonify, render_template, request

import image_processor
import ocr_service
from drugsetu_client import DrugSetuClient, DrugSetuError, DrugSetuNotConfigured

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


def create_app(drugsetu_client=None) -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES + 1024 * 64
    client = drugsetu_client or DrugSetuClient()

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
        return jsonify(status="ok", drugsetu_configured=client.configured)

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

        # ---- DrugSetu ----
        try:
            result = client.identify(parsed["names"], parsed["strengths"])
        except DrugSetuNotConfigured:
            return jsonify(status="api_not_configured", vision=vision, ocr=ocr_info, warnings=warnings,
                           message="The photo was read successfully, but the DrugSetu medicine database "
                                   "is not configured on this server, so no medicine details can be shown.")
        except DrugSetuError as exc:
            log.warning("DrugSetu error: %s", exc)
            return jsonify(status="api_error", vision=vision, ocr=ocr_info, warnings=warnings,
                           message=str(exc)), 502
        except Exception:
            log.exception("unexpected DrugSetu failure")
            return jsonify(status="api_error", vision=vision, ocr=ocr_info, warnings=warnings,
                           message="Medicine lookup failed. Please try again."), 502

        if result["status"] == "not_found":
            return jsonify(status="not_found", vision=vision, ocr=ocr_info, warnings=warnings,
                           message="No matching medicine was found in the database for the text read "
                                   "from the photo.")
        if ocr.mean_confidence < 0.6 and result["status"] == "confirmed":
            warnings.append("Text recognition confidence was low. Please check the name against your pack.")

        return jsonify(
            status=result["status"], vision=vision, ocr=ocr_info, warnings=warnings,
            match=public_match(result["match"]) if result["match"] else None,
            possible=[public_match(p) for p in result["possible"]],
            comparison=result["comparison"],
        )

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", 5000)), debug=False)
