"""
Uçtan uca test — path varyantı.
Trigger payload'unda image alanı base64 yerine dosya yolu ile yollanır.
VLM-GATE içeride path → bytes → base64 dönüşümünü yapıp VLM'e iletmeli.
"""

import asyncio
import json

import httpx

BASE = "http://127.0.0.1:8000"
IMG_PATH = r"Z:\20260422\20897\20260421234517_1776815117429.jpg"


def build_payload(image_value: str) -> dict:
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
    payload = build_payload(IMG_PATH)
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

        print("\n--- image API testleri ---")
        r = await client.get(f"{BASE}/image", params={"path": IMG_PATH})
        print(f"GET /image -> {r.status_code} ({len(r.content)} bytes, ct={r.headers.get('content-type')})")

        r = await client.get(f"{BASE}/image", params={"path": "C:/Windows/System32/drivers/etc/hosts"})
        print(f"GET /image (root dışı) -> {r.status_code} {r.text[:200]}")


if __name__ == "__main__":
    asyncio.run(main())
