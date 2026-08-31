import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from slowapi.errors import RateLimitExceeded

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.api.limiter import limiter, rate_limit_exceeded_handler
from src.api.routes import chat, consultation, vending
from src.consult.db import init_db
from src.rag.chain import get_llm, get_rewrite_llm, get_vector_store

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    app.state.vector_store = get_vector_store()
    app.state.llm = get_llm()
    app.state.rewrite_llm = get_rewrite_llm()
    yield


app = FastAPI(title="MOYAK 모약이 API", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 단계 전체 허용. 배포 시 프론트 도메인으로 제한할 것
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(consultation.router)
app.include_router(vending.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def chat_test_page():
    return (STATIC_DIR / "chat_test.html").read_text(encoding="utf-8")


@app.get("/consult-demo", response_class=HTMLResponse)
def consult_demo_page():
    return (STATIC_DIR / "consult_demo.html").read_text(encoding="utf-8")