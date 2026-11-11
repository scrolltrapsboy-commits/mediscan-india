# MediScan India

A college project: upload or capture a photo of an Indian medicine strip/box/bottle, read its printed text, and look the medicine up in the **DrugSetu** API.

Workflow: **medicine image → OpenCV preprocessing → OCR → DrugSetu search → medicine information + price comparison**

## Objectives
- Genuinely apply OpenCV image processing (ECE3107 Module 3) in a working web app.
- Live camera capture in the browser, simple and minimal UI (recreated from the supplied Google Stitch design).
- Show only data returned by DrugSetu; never invent medicines, prices or fields.

## Technology stack
| Layer | Used |
|---|---|
| Frontend | HTML5, CSS3, vanilla JavaScript. Styling is the Stitch design's Tailwind theme, **pre-built to `static/css/tailwind.css`** (no CDN, no Node needed to run) |
| Backend | Python, Flask, Gunicorn |
| Computer vision | OpenCV (`opencv-python-headless`) |
| OCR | RapidOCR (ONNX Runtime) - installs with pip, no system package needed |
| Medicine data | DrugSetu API (only source) |
| Camera | Browser `navigator.mediaDevices.getUserMedia()` |
| Hosting | Render |

## Project layout
`app.py` (routes) · `image_processor.py` (OpenCV) · `ocr_service.py` (OCR + text parsing) · `drugsetu_client.py` (API) · `templates/index.html` · `static/{css,js,assets}` · `tests/`.
Original design files are kept in `static/assets/original-design-assets/`, the original loader in `static/assets/capsule-loader/`.

## Run locally (Windows)
```bat
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install --no-deps rapidocr-onnxruntime==1.2.3
copy .env.example .env
```
Edit `.env` and set `DRUGSETU_API_KEY`. Then `python app.py` and open http://127.0.0.1:5000 (camera works on localhost).

## DrugSetu configuration - READ THIS
The official API reference is behind the DrugSetu dashboard login, and **no request to DrugSetu has been made by this project yet**. Public sources disagree on the host (`api.drugsetu.in/v1/medicines/search` vs `api.drugsetu.com/v1/products/search`); response field names are not public. So `drugsetu_client.py` is marked UNVERIFIED:
1. Set `DRUGSETU_API_KEY` (private key, `ds_...`; website keys `ds_pk_` are rejected).
2. Compare base URL, search/detail paths and auth header with your dashboard docs; override with `DRUGSETU_API_BASE_URL`, `DRUGSETU_SEARCH_PATH`, `DRUGSETU_DETAIL_PATH`, `DRUGSETU_AUTH_HEADER`.
3. Run one search and compare the real JSON with `FIELD_MAP` in `drugsetu_client.py`; fix key names there. Missing fields show "Information unavailable".
Without a key, the app still runs OpenCV + OCR and shows the text it read.

## How OpenCV is used
`image_processor.py`, called by `POST /api/identify`:
- `cv2.imdecode` - decode/validate the upload; corrupted files are rejected.
- `cv2.cvtColor()` - BGR → grayscale.
- `cv2.blur()` - 3×3 mean filter to reduce sensor noise while keeping text strokes.
- `cv2.Canny()` - edge map. Used to measure detail and find the packaging region (an optional tighter crop for OCR). It is **never** the OCR input and does not identify a medicine.
- OCR preparation - grayscale and noise-reduced versions are scored (sharpness relative to estimated noise); the best goes to OCR first, the other and the edge-derived crop are fallbacks. The original image is untouched; images are only downscaled above 2400 px and upscaled below 1200 px.
- A Laplacian-variance check flags blurry photos.

## How OCR works
RapidOCR reads the chosen image; low-confidence lines are dropped; text is cleaned; brand-name candidates are ranked by text size × confidence after removing boilerplate (batch no., MRP, "keep out of reach"…); strengths (e.g. `500 mg`) and manufacturer lines are extracted. A match is called **confirmed** only if the name similarity to a DrugSetu result is ≥ 0.80; otherwise the user sees "possible matches - not confirmed". Loose tablets with no printing cannot be identified from a photo.

## Price comparison rules
Rows appear only for products with identical active ingredients **and** strengths, the same dosage form and compatible route. Per-tablet price is computed only for solid forms with a known pack quantity. "Lowest Price" is flagged only when at least two comparable priced rows exist. DrugSetu search returns about 10 results, so alternatives may be limited.

## Camera
`camera.js` requests ideal 1920×1080 (the device may give less; the actual size is shown), rear camera first, Switch button when several cameras exist, JPEG quality 0.95 at native resolution. Permission-denied / no-camera / in-use errors are reported; all tracks are stopped on Close, after Capture, and on page leave. Needs HTTPS or localhost.

## ECE3107 MODULE 3 – ROBOT VISION IMPLEMENTATION
**Implemented:** OpenCV; `cv2.cvtColor()`; `cv2.blur()`; `cv2.Canny()`; camera image acquisition (browser camera → image frame sent to OpenCV); image preprocessing before OCR; morphology/contours for region finding.
**Not used (not applicable to a website):** ROS, Gazebo, OpenNI, PCL, cv_bridge, robot navigation.

## Deploy on Render
1. Push to GitHub (`.env` is git-ignored).
2. New → Blueprint → select the repo (uses `render.yaml`). The build installs RapidOCR and runs an import check; deployment fails if the OCR package is missing. Start command: `gunicorn app:app`; health check: `/healthz`.
3. If you created a regular Web Service rather than a Blueprint, open Settings → Build & Deploy and set the Build Command to:
   `python -m pip install --upgrade pip && python -m pip install -r requirements.txt && python -m pip install --no-deps rapidocr-onnxruntime==1.2.3 && python -c "from rapidocr_onnxruntime import RapidOCR; print('RapidOCR package import OK')"`
   Save and deploy the latest commit. A regular Web Service may not automatically apply changes in `render.yaml`.
3. Set `DRUGSETU_API_KEY` in the Render dashboard. Render serves HTTPS, so the camera works.
Free instances have limited RAM and sleep when idle; the first OCR request loads the model (slow). Check `/ocr-healthz` after deployment to verify the OCR engine can initialize. `/healthz` reports package installation without loading the model.

## Testing
```bat
pip install -r requirements-dev.txt
python -m pytest -q tests
```
Covers upload validation, corrupted images, grayscale/blur/Canny, real OCR on a rendered label, DrugSetu client structure with mocked HTTP (timeouts, 401/403/429/5xx), matching and comparison rules.
Not covered by automated tests: live DrugSetu calls (no key/docs), real phone cameras, iPhone Safari, Render deployment. Test these manually.

## Known limitations
OCR accuracy depends on photo quality; curved/shiny foil can fail. Information is for reference only and does not replace a doctor or pharmacist.
