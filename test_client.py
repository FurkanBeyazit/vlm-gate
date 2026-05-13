"""
Manuel test istemcisi.
LLM'in gerçekte göndereceği şekilde tam payload yollar, SSE stream'i dinler.
"""

import asyncio
import json

import httpx

BASE = "http://127.0.0.1:8000"

SAMPLE_PAYLOAD = {
    "info": {
        "event": {
            "detected_time": "UTC+0900:2024-07-08 15:01:50",
            "type": "화재",
            "image": "/9j/4AAQSkZJRgABAQEASABIAAD",
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
            "cam_name": "차번인식용 원무PTZ",
            "channel": 0,
            "management_code": "336",
            "model_name": "ONVIF",
            "node_id": 20027,
        },
        "type": "danusys",
    },
}


async def main():
    async with httpx.AsyncClient(timeout=None) as client:
        trig = await client.post(f"{BASE}/trigger", json=SAMPLE_PAYLOAD)
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
