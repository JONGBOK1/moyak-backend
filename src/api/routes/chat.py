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


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]
    evidence: list[EvidenceItem]


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
    return ChatResponse(answer=result["answer"], sources=result["sources"], evidence=result["evidence"])