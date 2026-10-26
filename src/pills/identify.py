"""알약 사진 → 후보 목록, 전 과정.

API(`/pills/identify`)와 실사진 채점(`scripts/check_pill_real.py`)이 **같은 과정**을 쓰도록
한곳에 모았다. 채점에서 좋아진 것이 그대로 API 의 성능이 되게 하려는 것이다.

    사진(앞뒤 여러 장 가능) ─ analyze_best ─ 모양·색·각인·로고
        └ search.find ─ 점수순 후보
            └ 1위가 확실하지 않으면 rerank ─ 후보들의 식약처 사진을 보여주고 다시 고르게 한다
"""
from __future__ import annotations

import datetime
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field

from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from src import config
from src.pills import search
from src.pills.vision import PillVision
from src.pills.vision_llm import analyze_best

#: 다시 고를 때 보여줄 후보 수. 많을수록 정답이 안에 들 확률은 오르지만 사진이 작아진다.
RERANK_POOL = 10
#: 1위가 각인까지 맞고 2위와 이만큼(화면 점수 기준) 벌어져 있으면 다시 고르지 않는다.
DECISIVE_GAP = 0.05

#: 알약 각인은 길어야 10자 안팎(실측 최대 12자)이다. 이보다 훨씬 길거나 단어가 많으면
#: 알약이 아니라 포장지·약갑의 설명 문구를 읽은 것이다.
_PACKAGING_CHARS = 30
_PACKAGING_WORDS = 6

Analyzer = Callable[[list[bytes]], tuple[PillVision, "str | None"]]
#: (사진들, 후보들, 판정) → 후보 번호(0부터)를 다시 매긴 순서. 못 하면 None.
Reranker = Callable[[list[bytes], list[search.Candidate], PillVision], "list[int] | None"]


@dataclass
class Identified:
    seen: PillVision
    imprint: str | None
    candidates: list[search.Candidate]
    notes: list[str] = field(default_factory=list)
    #: 판정한 엔진. 모델 이름(예: claude-opus-5-5) 또는 opencv+google
    engine: str = "opencv+google"
    #: 후보 사진을 보고 다시 골랐는지
    reranked: bool = False
    #: 1위가 확실한가(각인이 통째로 맞고 2위와 벌어짐). 화면에서 "이 약으로 보여요" 라고 말해도 되는 경우
    confident: bool = False


class DailyBudget:
    """서버 전체에서 하루에 LLM 으로 판정할 사진 수.

    `/pills/identify` 는 로그인 없이 누구나 부를 수 있고 사진 한 장마다 Claude 비용이 든다.
    IP 별 제한(라우터)만으로는 여러 IP 에서 들어오면 막을 수 없어서 서버 전체 상한을 둔다.
    넘으면 그날 남은 요청은 **실패시키지 않고** 예전 방식(OpenCV + Google)으로 처리한다.
    메모리에만 세므로 서버가 재시작되면 0 부터 다시 센다(Render 무료는 자주 재시작한다).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._day = datetime.date.today()
        self.used = 0

    def take(self) -> bool:
        with self._lock:
            today = datetime.date.today()
            if today != self._day:
                self._day, self.used = today, 0
            if self.used >= config.PILL_LLM_DAILY_LIMIT:
                return False
            self.used += 1
            return True


budget = DailyBudget()


def _find(session, **kw) -> list[search.Candidate]:
    """검색. `session` 이 연결을 여는 함수(`src.pills.db.read_only`)면 검색할 때만 연다 —
    LLM 을 부르는 몇 초 동안 팀 Supabase 연결을 붙잡고 있지 않으려는 것이다."""
    if callable(session) and not hasattr(session, "execute"):
        with session() as conn:
            return search.find(conn, **kw)
    return search.find(session, **kw)


def looks_like_packaging(imprint: str) -> bool:
    return len(imprint) > _PACKAGING_CHARS or len(imprint.split()) > _PACKAGING_WORDS


def is_decisive(cands: list[search.Candidate]) -> bool:
    """1위가 확실한가 — 각인이 통째로 맞았고 2위와 점수가 벌어져 있다.

    이럴 때는 다시 고르지 않는다. 호출 한 번이 더 들고, 이미 맞힌 것을 흔들 수 있다.
    """
    if not cands:
        return True
    top = cands[0]
    if not any(m in ("각인 일치", "각인 앞뒤 일치") for m in top.matched):
        return False
    return len(cands) == 1 or top.score - cands[1].score >= DECISIVE_GAP


def identify(
    session: Session | Connection | Callable[[], AbstractContextManager[Connection]],
    images: list[bytes],
    *,
    otc_only: bool = True,
    limit: int = 10,
    rerank: bool = True,
    analyzer: Analyzer | None = None,
    reranker: Reranker | None = None,
) -> Identified:
    """사진(앞뒤 여러 장 가능) → 후보 목록. `ValueError` 는 사진이 깨졌다는 뜻이다.

    `session` 에는 연결 대신 **연결을 여는 함수**(`src.pills.db.read_only`)를 넘겨도 된다 —
    라우터는 그렇게 해서 검색하는 순간에만 팀 Supabase 연결을 잡는다.
    """
    if analyzer is None:
        use_llm = budget.take()
        seen, imprint = analyze_best(images, llm=use_llm)
    else:                       # 채점 스크립트·테스트가 직접 넘긴 것 — 상한을 세지 않는다
        seen, imprint = analyzer(images)

    notes: list[str] = []
    llm = seen.detail.get("llm") or {}
    engine = (llm.get("usage") or {}).get("model") or "opencv+google"

    if (llm.get("kinds") or 1) > 1:
        # 한 사진에 서로 다른 약이 섞이면 모양·색·각인이 뒤섞여 어느 약도 못 찾는다.
        # 실제로 트리티코·아빌리파이를 한 장에 찍은 사진이 그랬다.
        notes.append("서로 다른 약이 함께 찍혔습니다. 한 종류만 찍으면 정확해집니다.")
    if seen.confidence < 0.4:
        notes.append("알약을 또렷하게 찾지 못했습니다. 배경이 단순한 곳에서 다시 찍어 보세요.")

    if not imprint and seen.detail.get("has_logo"):
        # 일반의약품의 13% 는 각인이 글자가 아니라 회사 로고다. 다시 찍어도 소용없다는 걸
        # 알려야 사용자가 사진만 계속 바꿔 찍지 않는다. 뒷면에 글자가 있을 수 있다.
        notes.append("글자 대신 로고가 새겨진 면입니다. 뒷면도 같이 올리면 정확해집니다.")
    elif not imprint:
        notes.append("각인을 읽지 못해 모양과 색으로만 찾았습니다. 후보가 많을 수 있습니다.")
    elif looks_like_packaging(imprint):
        # 포장지·약갑을 찍으면 설명 문구를 통째로 읽는다. 실측에서 블리스터 사진이
        # 'PARACETAMOL TABLE RANBAXY WELSET-650 COMPOSITION ...' 처럼 나왔다.
        notes.append("포장지나 약갑을 찍으신 것 같습니다. 알약만 꺼내서 찍으면 훨씬 정확합니다.")

    cands = _find(
        session, shape=seen.shape, color=seen.color,
        print_text=imprint, aspect=seen.aspect,
        has_logo=seen.detail.get("has_logo"),
        otc_only=otc_only, limit=max(limit, RERANK_POOL),
    )
    # 각인을 읽었는데 맞는 약이 없으면 `find` 가 모양·색으로만 다시 찾아 준다.
    if imprint and not any(m.startswith("각인") for c in cands for m in c.matched):
        notes.append(f"읽은 각인('{imprint}')과 맞는 약이 없어 모양과 색으로만 찾았습니다.")

    confident = is_decisive(cands) and bool(cands)
    reranked = False
    if (rerank and config.PILL_RERANK and engine.startswith("claude")
            and not is_decisive(cands)):
        if reranker is None:
            from src.pills.rerank import rerank as reranker
        order = reranker(images, cands[:RERANK_POOL], seen)
        if order:
            picked = [cands[i] for i in order]
            for c in picked:
                c.matched.append("사진 비교")
            cands = picked + [c for i, c in enumerate(cands) if i not in set(order)]
            reranked = True

    return Identified(seen=seen, imprint=imprint, candidates=cands[:limit],
                      notes=notes, engine=engine, reranked=reranked, confident=confident)
