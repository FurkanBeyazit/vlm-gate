"""
VLM endpoint keşif scripti — 2. tur.

İlk turdan öğrendik:
  - POST /describe sadece JSON kabul ediyor
  - 'info' alanı zorunlu (LLM payload'unun aynısı bekleniyor)

Bu turda: tam payload'u base64 image ve path image ile gönderip
hangisinin geçerli olduğunu (200 vs 400) görelim.
"""

import base64
from pathlib import Path

import httpx

VLM = "http://172.20.14.130:18080/describe"
IMG = Path(r"Z:\20260422\20897\20260421234517_1776815117429.jpg")


def show(label: str, resp: httpx.Response):
    print(f"\n=== {label} ===")
    print(f"status: {resp.status_code}")
    print(f"content-type: {resp.headers.get('content-type')}")
    print(f"body[:800]: {resp.text[:800]}")


def base_payload(image_value: str) -> dict:
    return {
        "info": {
            "event": {
                "detected_time": "UTC+0900:2024-07-08 15:01:50",
                "type": "화재",
                "image": image_value,
                "description": "",
                "start_time": "20260511-102930",
                "end_time": "20260511-102930",
                "snapshot_period": 10,
            }
        },
        "LLM": "url",
        "VLM": VLM,
        "service_name": "Ainos1",
        "version": 1001,
        "vms": {
            "detail": {
                "cam_name": "test_cam",
                "channel": 0,
                "management_code": "336",
                "model_name": "ONVIF",
                "node_id": 20027,
            },
            "type": "danusys",
        },
    }


def probe_path():
    payload = base_payload(str(IMG))
    with httpx.Client(timeout=60) as c:
        show("FULL payload, image = PATH string", c.post(VLM, json=payload))


def probe_base64():
    if not IMG.exists():
        print(f"\n[base64] atlandı — dosya yok: {IMG}")
        return
    b64 = base64.b64encode(IMG.read_bytes()).decode("ascii")
    payload = base_payload(b64)
    with httpx.Client(timeout=120) as c:
        show(f"FULL payload, image = BASE64 ({len(b64)} chars)", c.post(VLM, json=payload))


def probe_empty_image():
    payload = base_payload("")
    with httpx.Client(timeout=60) as c:
        show("FULL payload, image = '' (boş)", c.post(VLM, json=payload))


if __name__ == "__main__":
    print(f"image path exists: {IMG.exists()}")
    probe_empty_image()
    probe_path()
    probe_base64()
