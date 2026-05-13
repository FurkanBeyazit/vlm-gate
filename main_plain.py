"""
Aynı VLM-GATE — ama sse-starlette OLMADAN, saf FastAPI ile.
Karşılaştırma için: main.py'da EventSourceResponse var, burda elle yapıyoruz.

Çalıştır:  uvicorn main_plain:app --reload --port 8001
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from sample_data import pick_scenario

app = FastAPI(title="VLM-GATE (plain)")
SESSIONS: dict[str, dict] = {}
INTERVAL_SEC = 2.0


class TriggerRequest(BaseModel):
    node_id: int
    event_name: str
    detected_time: str


def now_utc_kr() -> str:
    return "UTC+0900:" + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def build_payload(session: dict, description: str) -> dict:
    return {
        "LLM": "url",
        "VLM": "http://172.20.14.130:18080/describe",
        "info": {
            "event": {
                "description": description,
                "detected_time": session["detected_time"],
                "start_time": session["start_time"],
                "end_time": now_utc_kr(),
                "image": "",
                "snapshot_period": int(INTERVAL_SEC),
                "type": session["event_name"],
            }
        },
        "service_name": "Ainos1",
        "version": 1001,
        "vms": {
            "detail": {
                "cam_name": "sample_cam",
                "channel": 0,
                "management_code": "336",
                "model_name": "ONVIF",
                "node_id": session["node_id"],
            },
            "type": "danusys",
        },
    }


def sse_format(event: str, data: dict) -> str:
    """
    SSE tel formatı:
      event: <event-name>\n
      data: <json>\n
      \n     <- boş satır mesajı bitirir
    """
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


@app.post("/trigger")
async def trigger(req: TriggerRequest):
    session_id = uuid.uuid4().hex
    SESSIONS[session_id] = {
        "node_id": req.node_id,
        "event_name": req.event_name,
        "detected_time": req.detected_time,
        "start_time": datetime.now().strftime("%Y%m%d-%H%M%S"),
        "scenario": pick_scenario(req.event_name),
    }
    return {"session_id": session_id, "stream_url": f"/stream/{session_id}"}


@app.get("/stream/{session_id}")
async def stream(session_id: str):
    if session_id not in SESSIONS:
        raise HTTPException(404, "session not found")
    session = SESSIONS[session_id]

    async def gen():
        for description in session["scenario"]:
            yield sse_format("vlm_description", build_payload(session, description))
            await asyncio.sleep(INTERVAL_SEC)
        yield sse_format("end", {"session_id": session_id, "status": "done"})
        SESSIONS.pop(session_id, None)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # nginx vb. buffer'lamasın
        },
    )
