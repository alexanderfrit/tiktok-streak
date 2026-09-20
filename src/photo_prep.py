"""Write the custom photo secret to a file before the streak run.

The setup GUI uploads a small image as a data URL secret (`PHOTO_DATA_URL`).
A runner can't receive a file through secrets, so this decodes the data URL to
`assets/custom_photo.<ext>` and exports `PHOTO_PATH` (via GITHUB_ENV) for
main.py. With no upload it keeps the shipped default photo, or a `PHOTO_PATH`
secret if one was set.

Run on the runner as:  python -m src.photo_prep
"""
import base64
import os
import re

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
        "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp"}
_DATA_URL = re.compile(r"data:(image/[a-z0-9.+-]+);base64,(.+)", re.DOTALL | re.IGNORECASE)
DEFAULT = "assets/default_photo.png"


def resolve(data_url: str, fallback: str = "") -> str:
    """Return the photo path to use; write the file when a data URL is given."""
    path = (fallback or "").strip() or DEFAULT
    m = _DATA_URL.match((data_url or "").strip())
    if m:
        ext = _EXT.get(m.group(1).lower(), "png")
        path = f"assets/custom_photo.{ext}"
        data = base64.b64decode(m.group(2))
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
    return path


def main() -> None:
    path = resolve(os.environ.get("PHOTO_DATA_URL", ""), os.environ.get("PHOTO_PATH", ""))
    genv = os.environ.get("GITHUB_ENV")
    if genv:
        with open(genv, "a", encoding="utf-8") as f:
            f.write(f"PHOTO_PATH={path}\n")
    print(f"PHOTO_PATH={path}")


if __name__ == "__main__":
    main()
