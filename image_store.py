"""
Mount edilmiş image deposu için yardımcılar.
Güvenlik: tüm path'ler IMAGE_ROOT altında olmalı.
Cache: VLM base64 image yollarsa IMAGE_CACHE_DIR altına kaydedilir.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

import config


class UnsafePathError(Exception):
    pass


IMAGE_ROOT = Path(config.IMAGE_ROOT).resolve()
IMAGE_CACHE_DIR = Path(config.IMAGE_CACHE_DIR).resolve()
IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def resolve_safe(path_str: str) -> Path:
    p = Path(path_str).resolve()
    try:
        p.relative_to(IMAGE_ROOT)
    except ValueError as e:
        raise UnsafePathError(f"path {p} is outside root {IMAGE_ROOT}") from e
    return p


def looks_like_base64(value: str) -> bool:
    return bool(value) and value.startswith(("/9j/", "iVBOR"))


def save_base64_image(b64: str) -> Path:
    """
    Base64 image'ı IMAGE_CACHE_DIR'a kaydet, dosya yolunu döner.
    Aynı içerik tekrar gelirse aynı dosyaya yazılır (hash bazlı isim).
    """
    suffix = ".jpg" if b64.startswith("/9j/") else ".png" if b64.startswith("iVBOR") else ".bin"
    digest = hashlib.sha256(b64.encode("ascii")).hexdigest()[:16]
    out_path = IMAGE_CACHE_DIR / f"{digest}{suffix}"
    if not out_path.exists():
        out_path.write_bytes(base64.b64decode(b64))
    return out_path
