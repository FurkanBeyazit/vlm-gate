"""
Uçtan uca test:
  - Z drive'daki yangın fotosunu base64'le
  - VLM-GATE'e POST /trigger
  - SSE stream'ini dinle, gelen description'ı bas

Çalışmadan önce: uvicorn main:app --reload --port 8000
"""

import asyncio
import base64
import json
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8000"
IMG = Path(r"Z:\20260422\20897\20260421234517_1776815117429.jpg")


def build_payload(image_b64: str) -> dict:
    return {
        "info": {
            "event": {
                "detected_time": "UTC+0900:2024-07-08 15:01:50",
                "type": "화재",
                "image": image_b64,
                "description": "",
                "start_time": "20260511-102930",
                "end_time": "20260511-102930",
                "snapshot_period": 10,
            }
        },
        "LLM": "url",
        "VLM": "http://172.20.14.130:18080/describe",
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


async def main():
    if not IMG.exists():
        print(f"HATA: foto yok — {IMG}")
        return
    b64 = base64.b64encode(IMG.read_bytes()).decode("ascii")
    print(f"image base64 length: {len(b64)}")

    payload = build_payload(b64)
    async with httpx.AsyncClient(timeout=None) as client:
        trig = await client.post(f"{BASE}/trigger", json=payload)
        trig.raise_for_status()
        session_id = trig.json()["session_id"]
        print(f"[trigger] session_id={session_id}\n")

        async with client.stream("GET", f"{BASE}/stream/{session_id}") as resp:
            event_name = None
            async for line in resp.aiter_lines():
                if not line:
                    event_name = None
                    continue
                if line.startswith("event:"):
                    event_name = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    raw = line.split(":", 1)[1].strip()
                    data = json.loads(raw)
                    if event_name == "vlm_description":
                        desc = data["info"]["event"]["description"]
                        print(f"[vlm] {desc}")
                    else:
                        print(f"[{event_name}] {data}")


if __name__ == "__main__":
    asyncio.run(main())
