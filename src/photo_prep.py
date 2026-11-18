"""Write the custom photo secret to a file before the streak run.

The setup GUI uploads a small image as a data URL secret (`PHOTO_DATA_URL`).
A runner can't receive a file through secrets, so this decodes the data URL to
`assets/custom_photo.<ext>` and exports `PHOTO_PATH` (via GITHUB_ENV) for
main.py. With no upload it keeps the shipped default photo, or a `PHOTO_PATH`
secret if one was set.

Run on the runner as:  python -m src.photo_prep
"""
import base64
import binascii
import os
import re

_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
        "image/webp": "webp", "image/gif": "gif", "image/bmp": "bmp"}
_DATA_URL = re.compile(r"data:(image/[a-z0-9.+-]+);base64,(.+)", re.DOTALL | re.IGNORECASE)
_DISABLE_SENTINELS = ("none", "off", "disabled")
_TRUTHY_VALUES = ("1", "true", "yes")
DEFAULT = "assets/default_photo.png"


def resolve(data_url: str, fallback: str = "", disable_flag: str = "") -> str:
    """Return the photo path to use; write the file when a data URL is given."""
    if (disable_flag or "").strip().lower() in _TRUTHY_VALUES:
        return ""

    raw_data = (data_url or "").strip()
    raw_fallback = (fallback or "").strip()
    if raw_data.lower() in _DISABLE_SENTINELS or raw_fallback.lower() in _DISABLE_SENTINELS:
        return ""

    path = raw_fallback or DEFAULT
    if not raw_data:
        return path

    m = _DATA_URL.match(raw_data)
    if not m:
        raise ValueError(
            "Invalid PHOTO_DATA_URL: expected a base64 image data URL "
            "(data:image/<type>;base64,<payload>)."
        )

    ext = _EXT.get(m.group(1).lower(), "png")
    b64_payload = re.sub(r"\s+", "", m.group(2))
    if not b64_payload:
        raise ValueError("Invalid PHOTO_DATA_URL: empty base64 payload.")

    try:
        data = base64.b64decode(b64_payload, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"Invalid base64 image data in PHOTO_DATA_URL: {e}") from e

    if not data:
        raise ValueError("Invalid PHOTO_DATA_URL: decoded image is empty.")

    path = f"assets/custom_photo.{ext}"
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def main() -> None:
    path = resolve(
        os.environ.get("PHOTO_DATA_URL", ""),
        os.environ.get("PHOTO_PATH", ""),
        os.environ.get("DISABLE_PHOTO", ""),
    )
    genv = os.environ.get("GITHUB_ENV")
    if genv:
        with open(genv, "a", encoding="utf-8") as f:
            f.write(f"PHOTO_PATH={path}\n")
    print(f"PHOTO_PATH={path}")


if __name__ == "__main__":
    main()
