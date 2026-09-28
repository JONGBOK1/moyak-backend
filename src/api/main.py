import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.api.limiter import limiter, rate_limit_exceeded_handler
from src.api.routes import chat, consultation, drugs, shop, vending
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
app.include_router(drugs.router)
app.include_router(vending.router)
app.include_router(shop.router)

app.mount("/kiosk", StaticFiles(directory=STATIC_DIR / "kiosk", html=True), name="kiosk")
app.mount("/app", StaticFiles(directory=STATIC_DIR / "app", html=True), name="app")
app.mount("/static/vendor", StaticFiles(directory=STATIC_DIR / "vendor"), name="vendor")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def root():
    # 서버 주소로 바로 들어오면 앱 첫 화면(스플래시)부터 보여준다.
    return RedirectResponse("/app/splash/")


@app.get("/chat-test", response_class=HTMLResponse)
def chat_test_page():
    return (STATIC_DIR / "chat_test.html").read_text(encoding="utf-8")


@app.get("/consult-demo", response_class=HTMLResponse)
def consult_demo_page():
    return (STATIC_DIR / "consult_demo.html").read_text(encoding="utf-8")


@app.get("/consult", response_class=HTMLResponse)
def consult_direct_page():
    """챗봇 대화 없이 바로 약사 화상 상담을 시작하는 진입점 (네비게이션바용)."""
    return (STATIC_DIR / "consult_direct.html").read_text(encoding="utf-8")


@app.get("/pharmacist", response_class=HTMLResponse)
def pharmacist_page():
    """약사 전용 대시보드: 대기 목록 처리 + 약품 검색 자동완성 + 처리 내역 조회."""
    return (STATIC_DIR / "pharmacist.html").read_text(encoding="utf-8")


@app.get("/scan", response_class=HTMLResponse)
def qr_scan_page():
    """실제 Flutter 앱이 나오기 전까지, 카메라로 자판기 QR을 스캔하는 웹 데모 (폰 앱 대역)."""
    return (STATIC_DIR / "qr_scan.html").read_text(encoding="utf-8")