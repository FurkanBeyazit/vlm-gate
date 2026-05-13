"""
VLM servis istemcisi.
Endpoint: http://172.20.14.130:18080/describe (Park Hoonbeom)

Kontrat:
  - POST + JSON body
  - Body, LLM payload yapısının aynısı; `info` zorunlu
  - `info.event.image` base64 zorunlu (path kabul etmez)
  - Response: aynı yapı, `info.event.description` doldurulmuş
"""

from copy import deepcopy

import httpx

import image_store

VLM_URL = "http://172.20.14.130:18080/describe"
TIMEOUT = 120.0


async def describe(payload: dict) -> dict:
    """
    Payload'u VLM'e yolla. `info.event.image` path ise önce base64'e çevir.
    """
    body = deepcopy(payload)
    body["info"]["event"]["image"] = image_store.to_base64(body["info"]["event"]["image"])

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(VLM_URL, json=body)
        resp.raise_for_status()
        return resp.json()
