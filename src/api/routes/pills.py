"""알약 사진으로 약 찾기.

프론트는 **사진만 보내면** 후보 목록을 받는다. 로그인은 필요 없다 —
약을 찾아보는 것 자체는 개인정보와 무관하고, 자판기 앞에서 바로 쓸 수 있어야 한다.

동작 순서
    사진 → 알약 영역 추출 → 모양·색 판별 → 각인 읽기(Google Vision)
         → Supabase `drug_pills` 조회 → 점수순 후보

실측 정확도 (식약처 사진, 일반의약품)
    모양 88.2% · 색 76.5%
    각인 없이:  후보에 정답  9.2% · 1위 정답  0.0%
    각인 포함:  후보에 정답 70.0% · 1위 정답 48.3% · 평균 후보 9.5개

즉 **각인이 읽히느냐가 전부다.** `GOOGLE_VISION_API_KEY` 가 없으면 모양·색만으로
후보를 내지만 쓸만한 수준이 못 된다.

DB는 팀 Supabase를 **읽기 전용**으로만 쓴다(지도·DUR 보강과 같은 원칙). 주소가 없거나
OpenCV가 설치돼 있지 않으면 이 라우터만 503을 돌려주고 나머지 API는 평소대로 동작한다.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import text

from src import config
from src.api.limiter import limiter

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/pills", tags=["pills"])

#: 폰 사진 한 장이면 충분하다. 더 크면 받기 전에 거절한다.
MAX_BYTES = 8 * 1024 * 1024

NO_DB = "알약 검색 DB가 설정되지 않았습니다. PILL_DATABASE_URL(또는 MAP_DATABASE_URL)을 확인해주세요."
NO_CV = "서버에 이미지 처리 라이브러리(opencv-python-headless)가 없습니다. requirements.txt를 확인해주세요."

# OpenCV는 무겁고 배포 환경에 따라 빠져 있을 수 있다. 여기서 실패해도 앱 전체가 죽지 않게
# 가져오기만 해 두고, 실제 호출 시점에 503으로 알린다.
try:
    from src.pills import search
    from src.pills.ocr import read_many
    from src.pills.vision import analyze

    _IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - 배포 환경 의존
    logger.warning("알약 인식 모듈을 불러오지 못했습니다: %s", exc)
    _IMPORT_ERROR = f"{NO_CV} ({type(exc).__name__})"


def _pill_database_url() -> str | None:
    # 대시보드에서 붙여넣을 때 끝에 줄바꿈/공백이 섞이는 일이 잦아 정리한다(map.py와 같은 이유).
    for value in (config.PILL_DATABASE_URL, os.getenv("DRUG_DATABASE_URL"), os.getenv("MAP_DATABASE_URL")):
        url = (value or "").strip()
        if url:
            return url
    return None


@lru_cache(maxsize=1)
def pill_engine():
    url = _pill_database_url()
    if not url:
        return None
    from src.map_database import create_database_engine

    return create_database_engine(url)


class Detected(BaseModel):
    shape: str = Field(description="원형 / 타원형 / 장방형 ...")
    color: str = Field(description="하양 / 노랑 / 분홍 ...")
    imprint: str | None = Field(default=None, description="읽어낸 각인. 못 읽으면 null")
    aspect: float | None = Field(
        default=None, description="가로세로비(긴변/짧은변). 사진 배율과 무관한 값"
    )
    confidence: float = Field(description="0~1. 낮으면 사진을 다시 찍는 게 낫다")


class CandidateOut(BaseModel):
    item_seq: str
    item_name: str
    entp_name: str | None = None
    drug_shape: str | None = None
    color_class1: str | None = None
    print_front: str | None = None
    print_back: str | None = None
    item_image: str | None = None
    etc_otc_name: str | None = None
    score: float
    matched: list[str] = Field(description="무엇이 맞았는지 — 모양 / 색 / 각인 일치")


class IdentifyResponse(BaseModel):
    detected: Detected
    candidates: list[CandidateOut]
    note: str | None = Field(
        default=None, description="주의할 점이 있으면 담긴다. 화면에 그대로 보여주면 된다"
    )


@router.post(
    "/identify",
    response_model=IdentifyResponse,
    summary="알약 사진으로 약 찾기",
    description=(
        "사진 한 장을 올리면 후보 목록을 돌려준다.\n\n"
        "**후보 1위를 정답으로 단정하지 말 것.** 실측 1위 정확도가 48% 라 "
        "절반은 틀린다. 화면에서는 사진과 함께 여러 개를 보여주고 "
        "사용자가 고르게 하는 것이 맞다."
    ),
)
@limiter.limit("20/minute;300/day")
async def identify(
    request: Request,
    file: UploadFile = File(description="알약 사진 (JPEG/PNG)"),
    otc_only: bool = Query(True, description="일반의약품만 찾기. 자판기 대상은 이쪽"),
    limit: int = Query(10, ge=1, le=50),
) -> IdentifyResponse:
    if _IMPORT_ERROR:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _IMPORT_ERROR)

    engine = pill_engine()
    if engine is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_DB)

    raw = await file.read()
    if not raw:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "빈 파일입니다.")
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"사진이 너무 큽니다 ({len(raw) // 1024 // 1024}MB). 8MB 이하로 줄여 주세요.",
        )

    try:
        seen = analyze(raw)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    notes: list[str] = []
    if seen.confidence < 0.4:
        notes.append("알약을 또렷하게 찾지 못했습니다. 배경이 단순한 곳에서 다시 찍어 보세요.")

    imprint = read_many(seen.crops) if seen.crops else None
    if not imprint:
        notes.append("각인을 읽지 못해 모양과 색으로만 찾았습니다. 후보가 많을 수 있습니다.")

    try:
        with engine.connect() as conn:
            # 팀 공용 Supabase는 절대 수정하지 않는다(지도·DUR 조회와 같은 원칙).
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = '10s'"))
            rows = search.find(
                conn, shape=seen.shape, color=seen.color,
                print_text=imprint, aspect=seen.aspect,
                otc_only=otc_only, limit=limit,
            )
    except Exception as exc:
        logger.warning("알약 후보 조회 실패: %s", type(exc).__name__)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "약품 정보를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.",
        ) from exc

    return IdentifyResponse(
        detected=Detected(
            shape=seen.shape, color=seen.color,
            imprint=imprint, aspect=seen.aspect, confidence=seen.confidence,
        ),
        candidates=[CandidateOut(**vars(c)) for c in rows],
        note=" ".join(notes) or None,
    )
