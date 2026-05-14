"""
Tüm URL/path config buradan okunur.
.env dosyası varsa otomatik yüklenir; yoksa env değişkenleri okunur;
o da yoksa default değerler (hep localhost) kullanılır.

Gerçek IP'leri .env dosyasına yaz, default'lara dokunma.
"""

from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# Bizim sunucumuzun dış dünyadan görüldüğü URL.
# LLM trigger + SSE + image API bu üzerinden çalışır.
VLM_GATE_BASE_URL = os.getenv("VLM_GATE_BASE_URL", "http://127.0.0.1:8000")

# VMS endpoint — gerçek değer .env'de.
VMS_URL = os.getenv("VMS_URL", "http://127.0.0.1:8001/from-vlm-gate")

# VLM endpoint — sadece fake_vms.py kullanır, asıl akışta gerek yok.
VLM_URL = os.getenv("VLM_URL", "http://127.0.0.1:8001/describe")

# /image endpoint'inin serve edebileceği klasörün kökü.
IMAGE_ROOT = os.getenv("VLM_GATE_IMAGE_ROOT", r"Z:\\")

# VLM base64 image gönderirse buraya kaydedilir.
IMAGE_CACHE_DIR = os.getenv("VLM_GATE_IMAGE_CACHE", r"Z:\vlm_gate_cache")

# Fake VMS örnek görsel kaynağı (dosya veya klasör).
SAMPLE_IMAGE_PATH = os.getenv("SAMPLE_IMAGE_PATH", r"Z:\20260422\20897")
