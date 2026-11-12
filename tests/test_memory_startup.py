"""Regression tests for lightweight Render startup."""
import os
import subprocess
import sys


def test_flask_startup_does_not_import_heavy_vision_stack():
    script = r'''
import sys
import app
assert "cv2" not in sys.modules, "OpenCV loaded during web startup"
assert "numpy" not in sys.modules, "NumPy loaded during web startup"
assert "rapidocr_onnxruntime" not in sys.modules, "RapidOCR loaded during web startup"
client = app.app.test_client()
response = client.get("/healthz")
assert response.status_code == 200
assert response.get_json()["status"] == "ok"
assert "cv2" not in sys.modules
assert "rapidocr_onnxruntime" not in sys.modules
'''
    env = os.environ.copy()
    env["PYTHONPATH"] = os.getcwd() + os.pathsep + env.get("PYTHONPATH", "")
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
