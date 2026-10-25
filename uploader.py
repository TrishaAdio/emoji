"""Upload files to catbox.moe, falling back to litterbox (temporary) if catbox refuses."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

CATBOX_API = "https://catbox.moe/user/api.php"
LITTERBOX_API = "https://litterbox.catbox.moe/resources/internals/api.php"
HEADERS = {"User-Agent": "emoji-pack-bot/1.0 (+https://catbox.moe)"}


class UploadError(Exception):
    pass


@dataclass
class UploadResult:
    url: str
    host: str  # "catbox" or "litterbox"
    expires: str | None = None  # e.g. "72h" for litterbox, None = permanent


def _post(url: str, data: dict, filename: str, content: bytes, mime: str) -> str:
    resp = requests.post(
        url,
        data=data,
        files={"fileToUpload": (filename, content, mime)},
        headers=HEADERS,
        timeout=120,
    )
    return resp.text.strip()


def upload(filename: str, content: bytes, mime: str = "application/octet-stream") -> UploadResult:
    """Upload to catbox.moe; if that fails, upload to litterbox for LITTERBOX_TIME (default 72h)."""
    errors = []

    try:
        body = _post(
            CATBOX_API,
            {"reqtype": "fileupload", "userhash": os.getenv("CATBOX_USERHASH", "")},
            filename, content, mime,
        )
        if body.startswith("https://"):
            return UploadResult(body, "catbox")
        errors.append(f"catbox: {body[:200]}")
    except requests.RequestException as exc:
        errors.append(f"catbox: {exc}")
    log.warning("catbox upload of %s failed (%s); trying litterbox", filename, errors[-1])

    ttl = os.getenv("LITTERBOX_TIME", "72h")
    try:
        body = _post(LITTERBOX_API, {"reqtype": "fileupload", "time": ttl}, filename, content, mime)
        if body.startswith("https://"):
            return UploadResult(body, "litterbox", ttl)
        errors.append(f"litterbox: {body[:200]}")
    except requests.RequestException as exc:
        errors.append(f"litterbox: {exc}")

    raise UploadError("; ".join(errors))
