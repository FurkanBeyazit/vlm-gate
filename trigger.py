"""
LLM trigger simulatörü.

Yeni LLM kontratı: {detected_time, type, channel, node_id}
type: FIRE / FALL / vb. (İngilizce — VMS isterse mapping yapar)

Kullanım:
  python trigger.py
  python trigger.py --node-id 30001 --type FIRE
  python trigger.py --count 5 --delay 1
"""

import argparse
import asyncio
from datetime import datetime, timezone

import httpx

import config

TRIGGER_URL = f"{config.VLM_GATE_BASE_URL}/trigger"


async def fire_one(client: httpx.AsyncClient, node_id: int, type_: str, channel: int, time_str: str):
    payload = {
        "detected_time": time_str,
        "type": type_,
        "channel": channel,
        "node_id": node_id,
    }
    print(f"[trigger] POST {TRIGGER_URL} {payload}")
    resp = await client.post(TRIGGER_URL, json=payload)
    print(f"[trigger] ← {resp.status_code} {resp.text}")


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

    async with httpx.AsyncClient(timeout=180) as client:
        for i in range(args.count):
            t = args.detected_time or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            node = args.node_id + i
            await fire_one(client, node, args.type, args.channel, t)
            if i < args.count - 1 and args.delay:
                await asyncio.sleep(args.delay)


if __name__ == "__main__":
    asyncio.run(main())
