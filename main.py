"""
VLM-GATE — LLM ile VMS/VLM arasındaki köprü servisi.

Endpoint'ler:
  POST /trigger        ← LLM'den gelir, VMS'e forward eder
  POST /vlm-result     ← VMS/VLM'den gelir, LLM şemasına çevirip SSE'ye yayar
  GET  /stream         ← LLM bağlanır, sonuçları sürekli alır (uzun süreli SSE)
  GET  /image?path=... ← Mounted klasörden foto serve eder
  DELETE /image?path=  ← Foto siler
  GET  /health         ← Servis durumu

Şema dönüşümü:
  - LLM'den gelen trigger: {detected_time, type, channel, node_id}
  - LLM'e giden SSE event: {detected_time, type, channel, node_id,
                            data: [{timestamp, description, api}]}
  - api: bizim /image endpoint'imizin tam URL'i (LLM oradan fotoyu çeker)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import config
import image_store

app = FastAPI(title="VLM-GATE")

SUBSCRIBERS: list[asyncio.Queue] = []
PENDING_TRIGGERS: dict[int, dict[str, Any]] = {}


class TriggerRequest(BaseModel):
    detected_time: str
    type: str
    channel: int
    node_id: int


def utc_now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def llm_time_to_vms_time(t: str) -> str:
    """LLM 'YYYYMMDDHHMMSS' (14 char) → VMS 'YYYYMMDD-HHMMSS'."""
    if len(t) == 14 and t.isdigit():
        return f"{t[:8]}-{t[8:]}"
    return t


def build_vms_payload(req: TriggerRequest) -> dict:
    """
    LLM şemasından VMS şemasına dönüştür.
    end_time / snapshot_period opsiyonel — VMS default davranır.
    type alanı VMS'in işine yaramaz, bizde tutuluyor.
    """
    return {
        "vms": {
            "detail": {
                "node_id": req.node_id,
                "channel": req.channel,
            }
        },
        "info": {
            "event": {
                "start_time": llm_time_to_vms_time(req.detected_time),
            }
        },
    }


def image_api_url(value: str) -> str:
    """
    Image alanı PATH ise → /image endpoint'ine URL üret.
    Boşsa → boş.
    (Base64 ise zaten extract aşamasında diske kaydedilip path'e dönüştürülmüş olur.)
    """
    if not value:
        return ""
    return f"{config.VLM_GATE_BASE_URL}/image?path={quote(value, safe='')}"


def build_llm_payload(trigger: dict[str, Any], description: str, image_path: str) -> dict:
    return {
        "detected_time": trigger["detected_time"],
        "type": trigger["type"],
        "channel": trigger["channel"],
        "node_id": trigger["node_id"],
        "data": [
            {
                "timestamp": utc_now_compact(),
                "description": description,
                "api": image_api_url(image_path),
            }
        ],
    }


@app.post("/trigger")
async def trigger(req: TriggerRequest):
    """
    LLM'den gelen trigger'ı VMS şemasına dönüştürüp ilet.
    type ve detected_time alanlarını PENDING_TRIGGERS'ta saklıyoruz —
    VLM push'u dönünce LLM'e geri yansıtacağız.
    """
    PENDING_TRIGGERS[req.node_id] = req.model_dump()
    vms_payload = build_vms_payload(req)

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(config.VMS_URL, json=vms_payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise HTTPException(502, f"VMS forward failed: {e}")
    return {
        "status": "forwarded",
        "vms_url": config.VMS_URL,
        "vms_payload": vms_payload,
        "subscribers": len(SUBSCRIBERS),
    }


def extract_node_id(vlm_payload: dict) -> int | None:
    try:
        return vlm_payload["vms"]["detail"]["node_id"]
    except (KeyError, TypeError):
        return None


def extract_description(vlm_payload: dict) -> str:
    try:
        return vlm_payload["info"]["event"]["description"]
    except (KeyError, TypeError):
        return ""


def extract_image_path(vlm_payload: dict) -> str:
    """
    image alanını çek. Base64 gelmişse cache klasörüne kaydedip path'i dön.
    Path gelmişse olduğu gibi dön.
    """
    try:
        value = vlm_payload["info"]["event"]["image"]
    except (KeyError, TypeError):
        return ""
    if not value:
        return ""
    if image_store.looks_like_base64(value):
        try:
            saved_path = image_store.save_base64_image(value)
            print(f"[vlm-result] base64 cached → {saved_path}")
            return str(saved_path)
        except Exception as e:
            print(f"[vlm-result] WARN: base64 save failed: {e}")
            return ""
    return value


@app.post("/vlm-result")
async def vlm_result(payload: dict[str, Any]):
    """
    VMS/VLM sonucu push eder. Pending trigger ile eşleştirip LLM şemasına çevir,
    tüm açık SSE subscriber'larına yay.
    """
    node_id = extract_node_id(payload)
    desc_preview = extract_description(payload)[:60]
    print(f"[vlm-result] PUSH alındı node_id={node_id} desc='{desc_preview}...'")

    trigger = PENDING_TRIGGERS.get(node_id) if node_id is not None else None

    if trigger is None:
        # Eşleşen trigger yok — yine de gönder, ama log et.
        # node_id varsa minimum trigger oluştur, yoksa boş alanlar.
        trigger = {
            "detected_time": "",
            "type": "",
            "channel": 0,
            "node_id": node_id or 0,
        }
        print(f"[vlm-result] WARN: no matching trigger for node_id={node_id}")

    out = build_llm_payload(
        trigger=trigger,
        description=extract_description(payload),
        image_path=extract_image_path(payload),
    )

    for q in list(SUBSCRIBERS):
        await q.put(out)

    return {"ok": True, "delivered_to": len(SUBSCRIBERS), "node_id": node_id}


@app.get("/stream")
async def stream():
    queue: asyncio.Queue = asyncio.Queue()
    SUBSCRIBERS.append(queue)

    async def event_gen():
        try:
            counter = 0
            yield {
                "event": "connected",
                "data": json.dumps({"msg": "subscribed to VLM stream"}),
            }
            while True:
                payload = await queue.get()
                yield {
                    "event": "vlm_description",
                    "id": str(counter),
                    "data": json.dumps(payload, ensure_ascii=False),
                }
                counter += 1
        finally:
            if queue in SUBSCRIBERS:
                SUBSCRIBERS.remove(queue)

    return EventSourceResponse(event_gen())


@app.get("/image")
async def get_image(path: str = Query(...)):
    try:
        p = image_store.resolve_safe(path)
    except image_store.UnsafePathError as e:
        raise HTTPException(403, str(e))
    if not p.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(p, media_type="image/jpeg")


@app.delete("/image")
async def delete_image(path: str = Query(...)):
    try:
        p = image_store.resolve_safe(path)
    except image_store.UnsafePathError as e:
        raise HTTPException(403, str(e))
    if not p.is_file():
        raise HTTPException(404, "image not found")
    p.unlink()
    return {"status": "deleted", "path": str(p)}


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "subscribers": len(SUBSCRIBERS),
        "pending_triggers": list(PENDING_TRIGGERS.keys()),
        "vms_url": config.VMS_URL,
        "vlm_url": config.VLM_URL,
        "image_root": str(image_store.IMAGE_ROOT),
    }
