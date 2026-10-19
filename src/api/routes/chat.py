from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from src.api.limiter import limiter
from src.rag.chain import ask

router = APIRouter()


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    history: list[Message] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    item_name: str
    field: str
    field_label: str
    text: str


class RecommendationItem(BaseModel):
    rank: int
    item_name: str
    reason: str = ""
    ingredient: str = ""
    caution: str = ""


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]
    evidence: list[EvidenceItem]
    # 증상 기반 추천 답변일 때만 채워짐(약마다 카드로 그리기용). 그 외 질문은 빈 목록 — answer를 그대로 보여주면 된다.
    recommendations: list[RecommendationItem] = []


@router.post("/chat", response_model=ChatResponse)
@limiter.limit("15/minute;200/day")
def chat(request: Request, payload: ChatRequest) -> ChatResponse:
    result = ask(
        payload.question,
        history=[m.model_dump() for m in payload.history],
        vector_store=request.app.state.vector_store,
        llm=request.app.state.llm,
        rewrite_llm=request.app.state.rewrite_llm,
    )
    return ChatResponse(answer=result["answer"], sources=result["sources"], evidence=result["evidence"],
                        recommendations=result.get("recommendations", []))