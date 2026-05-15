"""
Fake VMS — VLM-GATE'den gelen trigger'ı alır, VLM'in beklediği formatta
input hazırlar, callback_url ekleyerek VLM'e gönderir.
VLM cevabı doğrudan VLM-GATE'in /vlm-result endpoint'ine push eder; bizim
aradan herhangi bir iletim sorumluluğumuz yok.

Çalıştır:  uvicorn fake_vms:app --port 8001 --reload

Gerçek VMS hazır olunca: bu servis kapatılır, VLM-GATE'in VMS_URL'i
gerçek VMS'i gösterir. Kontrat: POST {detected_time, type, channel, node_id}.
"""

from __future__ import annotations

import base64
import random
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import config

app = FastAPI(title="Fake VMS")

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def pick_sample_image() -> Path:
    """SAMPLE_IMAGE_PATH klasörse içinden rastgele, dosyaysa kendisi."""
    p = Path(config.SAMPLE_IMAGE_PATH)
    if p.is_file():
        return p
    if p.is_dir():
        candidates = [c for c in p.iterdir() if c.suffix.lower() in IMAGE_EXTS]
        if not candidates:
            raise FileNotFoundError(f"no images in dir: {p}")
        return random.choice(candidates)
    raise FileNotFoundError(f"sample image path not found: {p}")


class VmsDetail(BaseModel):
    node_id: int
    channel: int


class VmsBlock(BaseModel):
    detail: VmsDetail


class EventBlock(BaseModel):
    start_time: str = ""


class InfoBlock(BaseModel):
    event: EventBlock = EventBlock()


class FromGate(BaseModel):
    """VLM-GATE'ten gelen VMS formatı payload."""
    vms: VmsBlock
    info: InfoBlock = InfoBlock()


def load_image_b64(p: Path) -> str:
    return base64.b64encode(p.read_bytes()).decode("ascii")


def build_vlm_input(req: FromGate, image_b64: str) -> dict:
    """
    VLM'e gönderilen payload.
    callback_url payload'da YOK — VLM kendi config dosyasından okuyor
    ("forwarder - No callback_url configured" log'undan kanıt).
    Park Hoonbeom config'ine callback_url eklediğinde forwarder push eder.
    """
    node_id = req.vms.detail.node_id
    channel = req.vms.detail.channel
    start_time = req.info.event.start_time or datetime.now().strftime("%Y%m%d-%H%M%S")

    return {
        "info": {
            "event": {
                "image": image_b64,
                "description": "",
                "start_time": start_time,
                "end_time": datetime.now().strftime("%Y%m%d-%H%M%S"),
                "snapshot_period": 10,
            }
        },
        "LLM": "url",
        "VLM": config.VLM_URL,
        "service_name": "Ainos1",
        "version": 1001,
        "vms": {
            "detail": {
                "cam_name": "fake_cam",
                "channel": channel,
                "management_code": "336",
                "model_name": "ONVIF",
                "node_id": node_id,
            },
            "type": "danusys",
        },
    }


@app.post("/from-vlm-gate")
async def from_vlm_gate(req: FromGate):
    """VLM-GATE'in trigger'ı buraya gelir. Image hazırla, VLM'e gönder, biz işten çıkarız."""
    node_id = req.vms.detail.node_id
    channel = req.vms.detail.channel

    try:
        image_path = pick_sample_image()
    except FileNotFoundError as e:
        raise HTTPException(500, str(e))

    print(f"[fake_vms] node={node_id} ch={channel} image={image_path}")

    image_b64 = load_image_b64(image_path)
    vlm_input = build_vlm_input(req, image_b64)

    async with httpx.AsyncClient(timeout=120) as client:
        try:
            vlm_resp = await client.post(config.VLM_URL, json=vlm_input)
            vlm_resp.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"VLM call failed: {e}")

    return {
        "ok": True,
        "vlm_status": vlm_resp.status_code,
        "note": "VLM forwarder push'una bekleniyor → /vlm-result (VLM'in config'inde olmalı)",
    }


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "vlm_url": config.VLM_URL,
        "sample_image": config.SAMPLE_IMAGE_PATH,
    }
