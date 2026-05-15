"""
LLM trigger simulatörü.

POST /trigger artık SSE döner:
  event: forwarded      → VMS'e iletildi
  event: vlm_description → VLM sonucu (bu gelince bağlantı kapanır)
  event: timeout        → 30s içinde sonuç gelmedi
  event: error          → VMS forward başarısız

Kullanım:
  python trigger.py
  python trigger.py --node-id 30001 --type FIRE
  python trigger.py --count 5 --delay 1
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timezone

import httpx

import config

TRIGGER_URL = f"{config.VLM_GATE_BASE_URL}/trigger"
MASK_THRESHOLD = 120


def mask_long(obj, threshold=MASK_THRESHOLD):
    """Uzun string'leri maskele (base64 vb.)."""
    if isinstance(obj, dict):
        return {k: mask_long(v, threshold) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_long(v, threshold) for v in obj]
    if isinstance(obj, str) and len(obj) > threshold:
        return f"<{len(obj)} chars>"
    return obj


async def fire_one(node_id: int, type_: str, channel: int, time_str: str):
    payload = {
        "detected_time": time_str,
        "type": type_,
        "channel": channel,
        "node_id": node_id,
    }
    print(f"\n[trigger] POST {TRIGGER_URL}")
    print(f"[trigger] payload: {payload}")

    current_event = "message"
    async with httpx.AsyncClient(timeout=60) as client:
        async with client.stream("POST", TRIGGER_URL, json=payload) as resp:
            print(f"[trigger] ← HTTP {resp.status_code} (SSE stream açıldı)")
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    current_event = line[len("event:"):].strip()
                elif line.startswith("data:"):
                    raw = line[len("data:"):].strip()
                    try:
                        data = json.loads(raw)
                        masked = mask_long(data)
                        print(f"[trigger] event={current_event} → {json.dumps(masked, ensure_ascii=False, indent=2)}")
                    except json.JSONDecodeError:
                        print(f"[trigger] event={current_event} → {raw}")

                    if current_event in ("vlm_description", "timeout", "error"):
                        print(f"[trigger] bağlantı kapandı (event={current_event})")
                        return


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-id", type=int, default=20887)
    parser.add_argument("--type", default="FIRE")
    parser.add_argument("--channel", type=int, default=0)
    parser.add_argument(
        "--detected-time",
        default=None,
        help="YYYYMMDDHHMMSS (14 char). Vermezsen şimdiki UTC zaman.",
    )
    parser.add_argument("--count", type=int, default=1)
    parser.add_argument("--delay", type=float, default=0.0)
    args = parser.parse_args()

    for i in range(args.count):
        t = args.detected_time or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        node = args.node_id + i
        await fire_one(node, args.type, args.channel, t)
        if i < args.count - 1 and args.delay:
            await asyncio.sleep(args.delay)


if __name__ == "__main__":
    asyncio.run(main())
