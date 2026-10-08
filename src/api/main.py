import sys
import os
import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from slowapi.errors import RateLimitExceeded

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.api.limiter import limiter, rate_limit_exceeded_handler
from src.api.routes import chat, consultation, conversation, drugs, vending_machines, drug_search, vending, shop
from src.api.routes import map as map_routes
from src.consult.db import init_db, SessionLocal, engine
from src.consult import worker
from src.consult.auth import local_identity_allowed

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv('MOYAK_LOCAL_IDENTITY') == '1' and engine.dialect.name != 'sqlite':
        raise RuntimeError('Local identity requires an isolated SQLite database')
    init_db()
    if os.getenv('MOYAK_LOCAL_DEMO') == '1' and engine.dialect.name == 'sqlite':
        from src.consult.map_seed import seed_demo_machines
        with SessionLocal() as db:
            seed_demo_machines(db)
    async def process():
        while True:
            try:
                processed = await run_in_threadpool(worker.process_one)
                app.state.worker_error = False
            except Exception:
                app.state.worker_error = True
                processed = False
            await asyncio.sleep(0.1 if processed else 1)
    task = asyncio.create_task(process())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="MOYAK 모약이 API", lifespan=lifespan)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[value.strip() for value in os.getenv('CORS_ORIGINS', '').split(',') if value.strip()],
    allow_origin_regex=os.getenv('CORS_ORIGIN_REGEX') or None,  # 예: Flutter 웹 개발 서버(포트가 매번 바뀜)
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat.router)
app.include_router(consultation.router)
app.include_router(conversation.router)
app.include_router(drugs.router)
app.include_router(drug_search.router)
# 키오스크·의약외품 구매 라우트는 팀 master 원본 그대로 (vending.py, shop.py)
app.include_router(vending.router)
app.include_router(shop.router)
app.include_router(vending_machines.router)
app.include_router(map_routes.router)

# Team HTML screens are available alongside the separate Flutter client.
app.mount('/app', StaticFiles(directory=STATIC_DIR / 'app', html=True), name='user-app')
app.mount('/kiosk', StaticFiles(directory=STATIC_DIR / 'kiosk', html=True), name='kiosk')
app.mount('/static/vendor', StaticFiles(directory=STATIC_DIR / 'vendor'), name='vendor')


@app.middleware('http')
async def local_identity_boundary(request: Request, call_next):
    if os.getenv('MOYAK_LOCAL_IDENTITY') == '1':
        if not local_identity_allowed(request):
            return JSONResponse({'detail': '로컬 시연은 이 PC에서만 접근할 수 있습니다.'}, status_code=403)
        host = request.url.hostname
        origin = request.headers.get('origin')
        if host not in {'127.0.0.1', 'localhost', '::1'} or (origin and origin != str(request.base_url).rstrip('/')):
            return JSONResponse({'detail': '허용되지 않은 시연 접근입니다.'}, status_code=403)
    return await call_next(request)


@app.get('/client-config')
def client_config(request: Request):
    return {'local_identity': local_identity_allowed(request)}


@app.get('/moyak-unified.js')
def unified_script():
    from fastapi.responses import FileResponse
    return FileResponse(STATIC_DIR / 'moyak_unified.js', media_type='application/javascript', headers={'Cache-Control': 'no-store'})


@app.get('/pharmacist', response_class=HTMLResponse)
def pharmacist_page():
    return (STATIC_DIR / 'pharmacist.html').read_text(encoding='utf-8')


@app.get('/scan', response_class=HTMLResponse)
def scanner_page():
    return (STATIC_DIR / 'qr_scan.html').read_text(encoding='utf-8')


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get('/', include_in_schema=False)
def root():
    return RedirectResponse('/chat-test')


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


@app.get("/vending-demo", response_class=HTMLResponse)
def vending_demo_page():
    return (STATIC_DIR / "vending_demo.html").read_text(encoding="utf-8")
