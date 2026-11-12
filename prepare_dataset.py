"""Hydrate the production medicine CSV from its public Git LFS object.

Render's source checkout can contain the small Git LFS pointer instead of the
actual CSV. This build step downloads and verifies the pinned LFS object.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys

import requests

ROOT = Path(__file__).resolve().parent
NAME = "updated_indian_medicine_data.csv"
EXPECTED_SHA256 = "e58c707e46524aa7e49afb1b2e320f68f8f524d135e014034ddc9968ef6dfa3"
EXPECTED_SIZE = 54_682_037
URL = (
    "https://media.githubusercontent.com/media/"
    "scrolltrapsboy-commits/mediscan-india/main/" + NAME
)


def valid(path: Path) -> bool:
    if not path.exists() or path.stat().st_size != EXPECTED_SIZE:
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest() == EXPECTED_SHA256


def main() -> None:
    target = ROOT / NAME
    if valid(target):
        print(f"Verified medicine CSV already present: {target.stat().st_size} bytes")
        return
    temporary = target.with_suffix(target.suffix + ".download")
    print("Downloading pinned medicine CSV Git LFS object...")
    digest = hashlib.sha256()
    size = 0
    try:
        with requests.get(URL, stream=True, timeout=(20, 180)) as response:
            response.raise_for_status()
            with temporary.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
        if size != EXPECTED_SIZE or digest.hexdigest() != EXPECTED_SHA256:
            raise RuntimeError(
                f"CSV integrity check failed (size={size}, sha256={digest.hexdigest()})"
            )
        os.replace(temporary, target)
        print(f"Downloaded and verified {NAME}: {size} bytes")
    except Exception:
        temporary.unlink(missing_ok=True)
        print(
            "Could not hydrate the medicine CSV from Git LFS. "
            "Deployment must not silently claim CSV enrichment is active.",
            file=sys.stderr,
        )
        raise


if __name__ == "__main__":
    main()
