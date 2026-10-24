import asyncio
import sys
from contextlib import suppress
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded
from starlette.concurrency import run_in_threadpool

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.api.limiter import limiter, rate_limit_exceeded_handler
from src.api.routes import auth, chat, consultation, conversation, drugs, shop, vending
from src.api.routes import care_profile
from src.api.routes import map as map_routes
from src.consult import worker
from src.consult.db import SessionLocal, init_db
from src.consult.map_seed import seed_demo_machines
from src.rag.chain import get_llm, get_rewrite_llm, get_vector_store

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with SessionLocal() as db:
        seed_demo_machines(db)  # 지도 시연용 가상 자판기 (이미 있으면 그대로 둠)
    app.state.vector_store = get_vector_store()
    app.state.llm = get_llm()
    app.state.rewrite_llm = get_rewrite_llm()

    # 상담 음성 전사·요약 초안 생성 워커 (DB 대기열을 1초마다 확인, 실패 작업은 재시도 가능 상태로 남음)
    async def process_queue():
        while True:
            try:
                processed = await run_in_threadpool(worker.process_one)
            except Exception:
                processed = False
            await asyncio.sleep(0.1 if processed else 1)

    task = asyncio.create_task(process_queue())
    async def expire_carts():
        from src.consult.shop import expire_orders
        def sweep():
            with SessionLocal() as db:
                expire_orders(db)
        while True:
            try:
                await run_in_threadpool(sweep)
            except Exception:
                pass  # DB failure leaves carts for the next transactional sweep.
            await asyncio.sleep(30)
    expiry_task = asyncio.create_task(expire_carts())
    try:
        yield
    finally:
        task.cancel()
        expiry_task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        with suppress(asyncio.CancelledError):
            await expiry_task


app = FastAPI(title="MOYAK 모약이 API", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 개발 단계 전체 허용. 배포 시 프론트 도메인으로 제한할 것
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(care_profile.router)
app.include_router(chat.router)
app.include_router(consultation.router)
app.include_router(conversation.router)
app.include_router(drugs.router)
app.include_router(vending.router)
app.include_router(shop.router)
app.include_router(map_routes.router)

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
