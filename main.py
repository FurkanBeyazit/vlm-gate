"""
VLM-GATE — LLM ile VMS/VLM arasındaki köprü servisi.

Endpoint'ler:
  POST /trigger        ← LLM'den gelir; SSE bağlantısı açar, VMS'e forward eder,
                         VLM sonucu gelince event yayıp bağlantıyı kapatır (30s timeout)
  POST /vlm-result     ← VMS/VLM'den gelir, LLM şemasına çevirip ilgili SSE'ye yayar
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
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import config
import image_store

app = FastAPI(title="VLM-GATE")

# node_id → (trigger_data, result_queue) — her /trigger isteği için bir kayıt
PENDING: dict[int, tuple[dict[str, Any], asyncio.Queue]] = {}

VLM_RESULT_TIMEOUT = 30  # saniye


class TriggerRequest(BaseModel):
    detected_time: str
    type: str
    channel: int
    node_id: int


KST = timezone(timedelta(hours=9))


def kst_now_compact() -> str:
    return datetime.now(KST).strftime("%Y%m%d%H%M%S")


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
                "timestamp": kst_now_compact(),
                "description": description,
                "api": image_api_url(image_path),
            }
        ],
    }


@app.post("/trigger")
async def trigger(request: Request, req: TriggerRequest):
    """
    LLM'den gelen trigger'ı VMS'e iletir ve SSE bağlantısını açık tutar.
    VLM sonucu /vlm-result üzerinden gelince vlm_description eventi yayıp kapanır.
    30 saniye içinde sonuç gelmezse timeout eventi gönderilir.
    Client erken koparsa finally bloğu PENDING'i temizler.
    """
    queue: asyncio.Queue = asyncio.Queue()
    PENDING[req.node_id] = (req.model_dump(), queue)

    vms_payload = build_vms_payload(req)

    async def event_gen():
        try:
            # VMS'e forward et
            async with httpx.AsyncClient(timeout=10) as client:
                try:
                    resp = await client.post(config.VMS_URL, json=vms_payload)
                    resp.raise_for_status()
                except httpx.HTTPError as e:
                    yield {"event": "error", "data": json.dumps({"message": f"VMS forward failed: {e}"})}
                    return

            yield {"event": "forwarded", "data": json.dumps({"status": "forwarded", "vms_url": config.VMS_URL})}

            # VLM sonucunu bekle; client kopunca asyncio.CancelledError fırlar → finally çalışır
            try:
                result = await asyncio.wait_for(queue.get(), timeout=VLM_RESULT_TIMEOUT)
                yield {
                    "event": "vlm_description",
                    "data": json.dumps(result, ensure_ascii=False),
                }
            except asyncio.TimeoutError:
                yield {"event": "timeout", "data": json.dumps({"message": f"no result in {VLM_RESULT_TIMEOUT}s"})}
        finally:
            PENDING.pop(req.node_id, None)

    # ping=86400: pratikte ping atmaz (bağlantı 30s'de kapanıyor zaten)
    return EventSourceResponse(event_gen(), ping=86400)


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
    VMS/VLM sonucu push eder. Bekleyen /trigger SSE bağlantısıyla eşleştirip sonucu iletir.
    """
    node_id = extract_node_id(payload)
    desc_preview = extract_description(payload)[:60]
    print(f"[vlm-result] PUSH alındı node_id={node_id} desc='{desc_preview}...'")

    pending = PENDING.get(node_id) if node_id is not None else None

    if pending is None:
        print(f"[vlm-result] WARN: no waiting trigger for node_id={node_id}")
        return {"ok": False, "reason": "no waiting trigger", "node_id": node_id}

    trigger_data, queue = pending

    out = build_llm_payload(
        trigger=trigger_data,
        description=extract_description(payload),
        image_path=extract_image_path(payload),
    )

    await queue.put(out)
    return {"ok": True, "node_id": node_id}



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
        "pending_node_ids": list(PENDING.keys()),
        "vms_url": config.VMS_URL,
        "vlm_url": config.VLM_URL,
        "image_root": str(image_store.IMAGE_ROOT),
    }
