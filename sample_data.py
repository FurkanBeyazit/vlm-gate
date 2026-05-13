"""
Önceden hazırlanmış sample senaryolar.
LLM testi sırasında gerçek VLM yerine bunları stream edeceğiz.
2 saniye aralıkla description listesi.
"""

FALL_SCENARIO = [
    "보도에서 한 사람이 걸어가고 있습니다.",
    "사람이 균형을 잃고 흔들리고 있습니다.",
    "사람이 바닥에 쓰러졌습니다. 움직임이 보이지 않습니다.",
    "쓰러진 사람 주변에 행인이 다가오고 있습니다.",
    "응급 상황으로 판단됩니다. 즉시 대응이 필요합니다.",
]

FIRE_SCENARIO = [
    "건물 창문에서 연기가 새어 나오고 있습니다.",
    "연기의 양이 급격히 증가하고 있습니다.",
    "창문 너머로 화염이 보이기 시작했습니다.",
    "화염이 외벽을 타고 확산되고 있습니다.",
    "대형 화재로 확대되었습니다. 긴급 대응 필요.",
]

SCENARIOS = {
    "fall": FALL_SCENARIO,
    "fire": FIRE_SCENARIO,
}


def pick_scenario(event_name: str) -> list[str]:
    name = event_name.lower()
    if "fall" in name or "쓰러" in name:
        return SCENARIOS["fall"]
    if "fire" in name or "화재" in name:
        return SCENARIOS["fire"]
    return SCENARIOS["fall"]
