from typing import Literal

from fastapi import APIRouter, Request, HTTPException
from threading import Lock
from openai import APIError, RateLimitError, AuthenticationError
from pydantic import BaseModel, Field

from src.api.limiter import limiter
from src.rag.chain import ask, get_vector_store, get_llm, get_rewrite_llm
from src import config

_initialize_lock = Lock()


def resources(app):
    with _initialize_lock:
        if not hasattr(app.state, 'vector_store'):
            if not config.OPENAI_API_KEY or not config.PINECONE_API_KEY:
                raise HTTPException(503, '챗봇의 OpenAI 또는 Pinecone 연결 설정이 필요합니다.')
            store, llm, rewrite = get_vector_store(), get_llm(), get_rewrite_llm()
            app.state.vector_store, app.state.llm, app.state.rewrite_llm = store, llm, rewrite
    return app.state.vector_store, app.state.llm, app.state.rewrite_llm

router = APIRouter()


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    history: list[Message] = Field(default_factory=list, max_length=20)


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
    if not payload.question.strip():
        raise HTTPException(422, '질문을 입력해주세요.')
    try:
        store, llm, rewrite = resources(request.app)
        result = ask(
            payload.question,
            history=[m.model_dump() for m in payload.history],
            vector_store=store, llm=llm, rewrite_llm=rewrite,
        )
    except HTTPException:
        raise
    except RateLimitError as error:
        raise HTTPException(503, 'AI 서비스 사용 한도 또는 크레딧을 확인해주세요. 잠시 후 다시 시도할 수 있습니다.') from error
    except AuthenticationError as error:
        raise HTTPException(503, 'AI 서비스 인증 설정을 확인해주세요.') from error
    except APIError as error:
        raise HTTPException(503, 'AI 서비스에 연결하지 못했습니다. 잠시 후 다시 시도해주세요.') from error
    except Exception as error:
        raise HTTPException(503, '챗봇 검색 서비스에 연결하지 못했습니다. 서버 설정과 검색 인덱스를 확인해주세요.') from error
    return ChatResponse(answer=result["answer"], sources=result["sources"], evidence=result["evidence"])
