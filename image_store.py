"""
Mount edilmiş image deposu için yardımcılar.
Güvenlik: tüm path'ler IMAGE_ROOT altında olmalı — dışarıdan arbitrary path okutma/sildirmenin önüne geçer.
"""

import base64
import os
from pathlib import Path

IMAGE_ROOT = Path(os.getenv("VLM_GATE_IMAGE_ROOT", r"Z:\\")).resolve()


class UnsafePathError(Exception):
    pass


def resolve_safe(path_str: str) -> Path:
    """
    Verilen path'i resolve et ve IMAGE_ROOT altında olduğunu doğrula.
    `..` tarzı çıkışları engeller.
    """
    p = Path(path_str).resolve()
    try:
        p.relative_to(IMAGE_ROOT)
    except ValueError as e:
        raise UnsafePathError(f"path {p} is outside root {IMAGE_ROOT}") from e
    return p


def looks_like_base64(value: str) -> bool:
    """JPEG ('/9j/') veya PNG ('iVBOR') header'ı varsa base64 say."""
    return bool(value) and value.startswith(("/9j/", "iVBOR"))


def to_base64(image_field: str) -> str:
    """
    `image` alanı zaten base64 ise olduğu gibi dön.
    Path ise dosyayı oku, base64'le.
    """
    if looks_like_base64(image_field):
        return image_field
    p = resolve_safe(image_field)
    if not p.is_file():
        raise FileNotFoundError(f"image file not found: {p}")
    return base64.b64encode(p.read_bytes()).decode("ascii")
