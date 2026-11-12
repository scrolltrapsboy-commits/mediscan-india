# MediScan India

MediScan India is a Flask web app that reads medicine packaging photos with OpenCV and RapidOCR, then uses Google's Gemini API to produce an AI-assisted, unverified medicine identification result.

## Workflow

`image upload or camera capture -> OpenCV preprocessing -> RapidOCR -> Gemini image + OCR analysis -> validated JSON -> existing Stitch UI`

OpenCV uses grayscale conversion, blur denoising, Canny edge analysis, morphology and contours to detect useful packaging regions. Canny edges are never sent as the medicine-identification input. Images are processed in memory and are not stored.

## Local setup on Windows

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install --no-deps rapidocr-onnxruntime==1.2.3
Copy-Item .env.example .env
```

Set a Gemini API key in `.env`:

```env
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-3.5-flash-lite
```

Start the server:

```powershell
.venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5000. Camera access works on localhost or HTTPS.

## Gemini configuration

The backend uses the official `google-genai` Python SDK. `GEMINI_MODEL` defaults to `gemini-3.5-flash-lite` and is configurable. Model availability and free-tier quota are account- and region-dependent; verify the selected model in Google AI Studio before deployment. The app does not automatically fall back to a paid model.

Bundled CSV datasets are loaded locally for enrichment and comparison: `updated_indian_medicine_data.csv` supplies composition, description, side effects, interactions, manufacturer, pack and price fields; `Extensive_A_Z_medicines_dataset_of_India.csv` is available as the broader catalog. CSV prices are displayed as unverified dataset values, not live market prices. Missing fields remain unavailable.

The API key is backend-only and must never be placed in HTML, JavaScript, or committed files. `.env` is ignored by Git. Missing keys, invalid credentials, unavailable models, quota exhaustion, timeouts, safety blocks and malformed responses are reported separately without exposing provider details.

## Safety and result policy

Gemini output is labelled AI-assisted and unverified. The service must use readable packaging evidence and must not confidently identify loose, unmarked tablets. Missing fields remain unavailable. It never provides dosing or personalised treatment advice, and it does not fabricate MRP, prices, pack quantities, medical facts or comparable-price rows. Verify any result with the package, a pharmacist or a doctor.

## API and health checks

`POST /api/identify` accepts the existing multipart `image` field and returns the existing `confirmed` / `possible` result shape plus explicit Gemini error statuses.

- `GET /healthz` reports application status, Gemini configuration presence, selected model and OCR package availability.
- `GET /ocr-healthz` initializes RapidOCR and reports engine readiness.

Health checks never call Gemini and never expose the API key.

## Render deployment

The included `render.yaml` installs the headless OpenCV and RapidOCR dependencies and runs Gunicorn. In the Render Dashboard, set:

```text
GEMINI_API_KEY=<your Gemini API key>
GEMINI_MODEL=gemini-3.5-flash-lite
```

Do not put the real key in `render.yaml`, `.env.example`, source code or Git history. Render camera access works over HTTPS. No external medicine database account or key is required.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest -q tests
```

The suite covers image validation, OpenCV/OCR behavior, Gemini structured-response normalization, missing configuration, malformed responses, possible and unidentified medicines, auth/quota/network errors, safety-compatible no-price behavior and frontend response compatibility. Gemini calls are mocked; tests do not consume API quota.

## Project layout

- `app.py`: Flask routes, validation and pipeline orchestration
- `gemini_client.py`: Gemini SDK adapter, schema validation and safe error mapping
- `image_processor.py`: OpenCV preprocessing and packaging-region detection
- `ocr_service.py`: RapidOCR extraction and text parsing
- `templates/index.html`: existing Google Stitch layout
- `static/js/`: upload, camera, loader and result rendering
- `tests/`: automated tests
