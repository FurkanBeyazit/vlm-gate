"""
LLM rolü test scripti — VLM-GATE'in SSE stream'ine bağlanır.

İki mod:
  python listen_sse.py            # ham SSE çıktısı (telde ne giderse)
  python listen_sse.py --pretty   # parse edip okunaklı bas, base64 image'ı maskele

Çıkış için Ctrl+C.
"""

from __future__ import annotations

import argparse
import asyncio
import json

import httpx

import config

STREAM_URL = f"{config.VLM_GATE_BASE_URL}/stream"


def mask_long_strings(obj, limit: int = 120):
    """Recursively replace strings longer than `limit` with a short summary."""
    if isinstance(obj, str):
        if len(obj) > limit:
            return f"<{len(obj)} chars: {obj[:40]}...>"
        return obj
    if isinstance(obj, dict):
        return {k: mask_long_strings(v, limit) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_long_strings(v, limit) for v in obj]
    return obj


def print_pretty(event: str | None, event_id: str | None, raw_data: str):
    try:
        data = json.loads(raw_data)
    except json.JSONDecodeError:
        print(f"[{event}] (non-json) {raw_data}")
        return

    if event == "vlm_description":
        masked = mask_long_strings(data)
        print(f"\n=== event=vlm_description id={event_id} ===")
        print(json.dumps(masked, ensure_ascii=False, indent=2))
    elif event == "connected":
        print(f"[connected] {data}")
    else:
        print(f"[{event}] {json.dumps(data, ensure_ascii=False)}")


async def stream_pretty(client: httpx.AsyncClient):
    async with client.stream("GET", STREAM_URL) as resp:
        event_name = None
        event_id = None
        async for line in resp.aiter_lines():
            if not line:
                event_name = None
                event_id = None
                continue
            if line.startswith("event:"):
                event_name = line.split(":", 1)[1].strip()
            elif line.startswith("id:"):
                event_id = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                raw = line.split(":", 1)[1].strip()
                print_pretty(event_name, event_id, raw)


async def stream_raw(client: httpx.AsyncClient):
    async with client.stream("GET", STREAM_URL) as resp:
        async for line in resp.aiter_lines():
            if line:
                print(line)
            else:
                print()


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pretty", action="store_true", help="parse and mask long strings")
    args = parser.parse_args()

    print(f"[listen] connecting → {STREAM_URL}")
    print(f"[listen] mode = {'pretty' if args.pretty else 'raw'}\n")

    async with httpx.AsyncClient(timeout=None) as client:
        if args.pretty:
            await stream_pretty(client)
        else:
            await stream_raw(client)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[listen] kapatıldı")
