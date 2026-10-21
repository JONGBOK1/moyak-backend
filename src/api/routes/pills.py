"""알약 사진으로 약 찾기.

프론트는 **사진만 보내면**(한 장, 또는 앞면+뒷면 두 장) 후보 목록을 받는다. 로그인은 필요 없다 —
약을 찾아보는 것 자체는 개인정보와 무관하고, 자판기 앞에서 바로 쓸 수 있어야 한다.

동작 순서 (전 과정은 `src/pills/identify.py`)
    사진 → Claude 가 모양·색·각인·로고를 한 번에 판정 → Supabase `drug_pills` 조회 → 점수순 후보
         └ 1위가 확실하지 않으면 상위 10개 후보의 식약처 사진을 보여주고 다시 고르게 한다
    Claude 가 안 되면(키 없음·네트워크·하루 상한 초과) 예전 방식: OpenCV 모양·색 + Google Vision 각인

실측 (docs/pills-api.md 에 자세히)
    식약처 사진 119장, 일반의약품    1위 93.3% · 3위 안 93.3%
    실사진 14장, 전체 의약품        3위 안 71.4% (앞뒤 두 장이면 약 기준 10개 중 9개)

챗봇에서 쓰는 법: 사진 → 이 API → 후보 카드 → 사용자가 고른 약 이름으로 `/chat` 질문.
1위를 정답으로 단정하지 않고 사용자가 고르게 하는 것이 원칙이다(`confident` 가 참일 때만 예외적으로
"이 약으로 보여요" 라고 말해도 된다).

DB는 팀 Supabase를 **읽기 전용**으로만 쓴다(지도·DUR 보강과 같은 원칙). 주소가 없거나
OpenCV가 설치돼 있지 않으면 이 라우터만 503을 돌려주고 나머지 API는 평소대로 동작한다.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.api.limiter import limiter
from src.pills.db import NO_DB, engine as pill_engine, read_only

logger = logging.getLogger("uvicorn.error")
router = APIRouter(prefix="/pills", tags=["pills"])

#: 폰 사진 한 장이면 충분하다. 더 크면 받기 전에 거절한다.
MAX_BYTES = 8 * 1024 * 1024

NO_CV = "서버에 이미지 처리 라이브러리(opencv-python-headless)가 없습니다. requirements.txt를 확인해주세요."

# OpenCV는 무겁고 배포 환경에 따라 빠져 있을 수 있다. 여기서 실패해도 앱 전체가 죽지 않게
# 가져오기만 해 두고, 실제 호출 시점에 503으로 알린다.
try:
    from src.pills import identify as pill

    _IMPORT_ERROR: str | None = None
except Exception as exc:  # pragma: no cover - 배포 환경 의존
    logger.warning("알약 인식 모듈을 불러오지 못했습니다: %s", exc)
    _IMPORT_ERROR = f"{NO_CV} ({type(exc).__name__})"


class Detected(BaseModel):
    shape: str = Field(description="원형 / 타원형 / 장방형 ...")
    color: str = Field(description="하양 / 노랑 / 분홍 ...")
    imprint: str | None = Field(default=None, description="읽어낸 각인. 못 읽으면 null")
    aspect: float | None = Field(
        default=None, description="가로세로비(긴변/짧은변). 사진 배율과 무관한 값"
    )
    confidence: float = Field(description="0~1. 낮으면 사진을 다시 찍는 게 낫다")
    engine: str = Field(
        default="opencv+google",
        description="판정한 엔진. 모델 이름(예: claude-opus-5-5) 또는 opencv+google(예비)",
    )
    reranked: bool = Field(
        default=False,
        description="1위가 확실하지 않아 후보들의 식약처 사진과 비교해 순위를 다시 매겼는지",
    )


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
    matched: list[str] = Field(
        description="무엇이 맞았는지 — 모양 / 색 / 각인 일치 / 로고 / 사진 비교 ...")


class IdentifyResponse(BaseModel):
    detected: Detected
    candidates: list[CandidateOut]
    confident: bool = Field(
        default=False,
        description="1위가 확실한가(각인이 통째로 맞고 2위와 점수가 벌어짐). "
                    "거짓이면 화면에서 반드시 사용자가 고르게 한다",
    )
    message: str = Field(
        default="", description="챗봇 말풍선에 그대로 띄울 한 줄 안내")
    note: str | None = Field(
        default=None, description="주의할 점이 있으면 담긴다. 화면에 그대로 보여주면 된다"
    )


def _message(r) -> str:
    if not r.candidates:
        return "사진에서 약을 찾지 못했어요. 알약을 밝은 곳에서 가까이, 한 알만 찍어 주세요."
    if r.confident:
        return (f"사진 속 약은 '{r.candidates[0].item_name}'(으)로 보여요. "
                "맞으면 눌러서 자세히 물어보세요.")
    return "사진과 비슷한 약을 찾았어요. 사진만으로는 확실하지 않으니, 맞는 약을 눌러 주세요."


@router.post(
    "/identify",
    response_model=IdentifyResponse,
    summary="알약 사진으로 약 찾기",
    description=(
        "사진 한 장(또는 앞면·뒷면 두 장)을 올리면 후보 목록을 돌려준다. 5~15초 걸린다.\n\n"
        "**후보 1위를 정답으로 단정하지 말 것.** `confident` 가 거짓이면 화면에서 "
        "여러 개를 보여주고 사용자가 고르게 한다. 고른 약 이름으로 `/chat` 에 질문하면 된다."
    ),
)
# 사진 한 장마다 Claude 비용(약 2센트)이 든다. IP 별 제한에 더해 서버 전체 하루 상한
# (PILL_LLM_DAILY_LIMIT, 넘으면 예전 방식으로 처리)도 있다 — src/pills/identify.py.
@limiter.limit("10/minute;100/day")
async def identify(
    request: Request,
    file: UploadFile = File(description="알약 사진 (JPEG/PNG)"),
    back: UploadFile | None = File(
        None, description="같은 알약의 뒷면 사진 (선택). 앞면이 로고뿐일 때 특히 도움이 된다"),
    otc_only: bool = Query(True, description="일반의약품만 찾기. 자판기 대상은 이쪽, 챗봇은 false 권장"),
    limit: int = Query(10, ge=1, le=50),
) -> IdentifyResponse:
    if _IMPORT_ERROR:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _IMPORT_ERROR)
    if pill_engine() is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, NO_DB)

    images = []
    for f in (file, back):
        if f is None:
            continue
        raw = await f.read()
        if not raw:
            if f is back:
                continue            # 프론트가 빈 칸을 보내도 앞면만으로 찾는다
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "빈 파일입니다.")
        if len(raw) > MAX_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"사진이 너무 큽니다 ({len(raw) // 1024 // 1024}MB). 8MB 이하로 줄여 주세요.",
            )
        images.append(raw)

    try:
        # **스레드에서 돌린다.** 몇 초씩 걸리는 외부 호출이라, 이 async 함수 안에서
        # 그대로 부르면 그동안 서버가 다른 요청(챗봇 포함)을 하나도 못 받는다.
        # DB 는 검색하는 순간에만 읽기 전용으로 연다(read_only 를 함수째 넘긴다).
        r = await run_in_threadpool(
            lambda: pill.identify(read_only, images, otc_only=otc_only, limit=limit))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except Exception as exc:
        logger.warning("알약 인식 실패: %s", type(exc).__name__)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "약품 정보를 불러오지 못했습니다. 잠시 후 다시 시도해주세요.",
        ) from exc

    return IdentifyResponse(
        detected=Detected(
            shape=r.seen.shape, color=r.seen.color,
            imprint=r.imprint, aspect=r.seen.aspect, confidence=r.seen.confidence,
            engine=r.engine, reranked=r.reranked,
        ),
        candidates=[CandidateOut(**vars(c)) for c in r.candidates],
        confident=r.confident,
        message=_message(r),
        note=" ".join(r.notes) or None,
    )
