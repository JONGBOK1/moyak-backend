from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from src.rag.chain import ask

router = APIRouter()


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)


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
def chat(payload: ChatRequest, request: Request) -> ChatResponse:
    result = ask(
        payload.question,
        retriever=request.app.state.retriever,
        llm=request.app.state.llm,
    )
    return ChatResponse(answer=result["answer"], sources=result["sources"], evidence=result["evidence"])