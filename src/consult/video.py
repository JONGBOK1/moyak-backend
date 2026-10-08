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
_PRESENCE_CACHE: dict[str, tuple[float, int | None]] = {}
_PRESENCE_TTL_SECONDS = 2  # 여러 화면이 3초마다 물어봐도 Daily API는 방마다 최대 2초에 한 번만 호출


def room_participant_count(room_url: str | None) -> int | None:
    """화상방에 지금 접속해 있는 인원 수 (Daily REST `GET /rooms/{name}/presence`).
    키가 없거나 조회에 실패하면 None — 화면은 알림만 못 띄울 뿐 상담 흐름은 그대로 진행된다."""
    if not room_url or not config.DAILY_API_KEY:
        return None
    room_name = room_url.rstrip("/").rsplit("/", 1)[-1]
    cached = _PRESENCE_CACHE.get(room_name)
    if cached and time.time() - cached[0] < _PRESENCE_TTL_SECONDS:
        return cached[1]
    try:
        res = requests.get(
            f"https://api.daily.co/v1/rooms/{room_name}/presence",
            headers={"Authorization": f"Bearer {config.DAILY_API_KEY}"},
            timeout=5,
        )
        res.raise_for_status()
        count = int(res.json().get("total_count", 0))
    except (requests.exceptions.RequestException, ValueError):
        count = None
    _PRESENCE_CACHE[room_name] = (time.time(), count)
    return count
