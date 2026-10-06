"""Daily.co 화상 상담방 생성.

방 생성이 실패해도(키 미설정, 네트워크 오류 등) 상담 요청 자체는 계속 진행되어야 하므로
예외를 여기서 흡수하고 None을 반환한다 — 화상 상담은 텍스트 상담 위에 얹힌 부가 기능이지,
그것 때문에 전체 흐름이 막히면 안 된다.
"""

import time

import requests

from src import config

ROOM_TTL_SECONDS = 2 * 60 * 60  # 2시간 후 자동 만료


def create_room() -> str | None:
    if not config.DAILY_API_KEY:
        return None
    try:
        res = requests.post(
            "https://api.daily.co/v1/rooms",
            headers={"Authorization": f"Bearer {config.DAILY_API_KEY}"},
            json={"properties": {"max_participants": 2, "exp": int(time.time()) + ROOM_TTL_SECONDS}},
            timeout=10,
        )
        res.raise_for_status()
        return res.json()["url"]
    except requests.exceptions.RequestException:
        return None