"""
VLM-GATE — LLM ile VLM arasındaki köprü.

SSE: sse-starlette kullanılıyor (FastAPI native SSE 0.135+ ister, env Python<3.10).

Akış:
  1) LLM  -> POST /trigger  (tam payload, info.event.image base64)
  2) VLM-GATE event'i kaydeder, session_id döner.
  3) LLM  -> GET /stream/{session_id}  (SSE bağlantısı açar)
  4) VLM-GATE:
       - USE_MOCK=True  -> sample_data'dan 2sn aralıklı description'lar akıtır
       - USE_MOCK=False -> VLM'i çağırır, dönen tek description'ı yayar
"""

import asyncio
import json
import os
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, AsyncIterable

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

import image_store
import vlm_client
from sample_data import pick_scenario

app = FastAPI(title="VLM-GATE")

SESSIONS: dict[str, dict[str, Any]] = {}
INTERVAL_SEC = 2.0
USE_MOCK = os.getenv("VLM_GATE_MOCK", "false").lower() in ("1", "true", "yes")


class EventDetail(BaseModel):
    detected_time: str
    type: str
    image: str
    description: str = ""
    start_time: str
    end_time: str
    snapshot_period: int


class EventWrapper(BaseModel):
    event: EventDetail


class VmsDetail(BaseModel):
    cam_name: str
    channel: int
    management_code: str
    model_name: str
    node_id: int


class Vms(BaseModel):
    detail: VmsDetail
    type: str


class TriggerPayload(BaseModel):
    info: EventWrapper
    LLM: str
    VLM: str
    service_name: str
    version: int
    vms: Vms


class TriggerResponse(BaseModel):
    session_id: str
    stream_url: str


def now_utc_kr() -> str:
    return "UTC+0900:" + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


@app.post("/trigger", response_model=TriggerResponse)
async def trigger(payload: TriggerPayload):
    session_id = uuid.uuid4().hex
    SESSIONS[session_id] = {
        "payload": payload.model_dump(),
        "scenario": pick_scenario(payload.info.event.type),
    }
    return TriggerResponse(
        session_id=session_id,
        stream_url=f"/stream/{session_id}",
    )


async def mock_stream(session: dict) -> AsyncIterable[dict]:
    for desc in session["scenario"]:
        out = deepcopy(session["payload"])
        out["info"]["event"]["description"] = desc
        out["info"]["event"]["end_time"] = now_utc_kr()
        yield out
        await asyncio.sleep(INTERVAL_SEC)


async def real_vlm_stream(session: dict) -> AsyncIterable[dict]:
    result = await vlm_client.describe(session["payload"])
    yield result


@app.get("/stream/{session_id}")
async def stream(session_id: str):
    if session_id not in SESSIONS:
        raise HTTPException(404, "session not found")
    session = SESSIONS[session_id]
    source = mock_stream(session) if USE_MOCK else real_vlm_stream(session)

    async def event_gen():
        i = 0
        async for payload in source:
            yield {
                "event": "vlm_description",
                "id": str(i),
                "data": json.dumps(payload, ensure_ascii=False),
            }
            i += 1
        yield {
            "event": "end",
            "data": json.dumps({"session_id": session_id, "status": "done"}),
        }
        SESSIONS.pop(session_id, None)

    return EventSourceResponse(event_gen())


@app.get("/image")
async def get_image(path: str = Query(..., description="image path under IMAGE_ROOT")):
    try:
        p = image_store.resolve_safe(path)
    except image_store.UnsafePathError as e:
        raise HTTPException(403, str(e))
    if not p.is_file():
        raise HTTPException(404, "image not found")
    return FileResponse(p, media_type="image/jpeg")


@app.delete("/image")
async def delete_image(path: str = Query(..., description="image path under IMAGE_ROOT")):
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
        "active_sessions": len(SESSIONS),
        "mock": USE_MOCK,
        "image_root": str(image_store.IMAGE_ROOT),
    }
