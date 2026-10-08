"""Explicit local demo entrypoint; never mounted on the production application."""
import asyncio
import os
import uuid
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from src import config
from src.api.routes import consultation, conversation, chat
from src.api.limiter import limiter, rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from src.api.routes import workflow_demo, vending, shop, drug_search, drugs
from src.api.routes import map as map_routes
from src.consult import conversation as service, video, worker
from src.consult.auth import Actor, current_actor, issue_token, verify_token
from src.consult.db import get_db, init_db, SessionLocal
from src.consult.models import ConsultationRequest


async def work():
    while True:
        try:
            processed = await run_in_threadpool(worker.process_one)
        except Exception:
            processed = False
        await asyncio.sleep(0.1 if processed else 1)


@asynccontextmanager
async def lifespan(app):
    expected = f"sqlite:///{config.BASE_DIR / 'data' / 'consult-demo.db'}"
    if os.getenv("MOYAK_LOCAL_DEMO") != "1" or config.DATABASE_URL != expected:
        raise RuntimeError("Use python scripts/run_consult_demo.py for an isolated local demo")
    if len(config.CONSULT_AUTH_SECRET) < 32:
        raise RuntimeError("Set CONSULT_AUTH_SECRET (at least 32 characters) in .env")
    init_db()
    task = asyncio.create_task(work())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="MOYAK 로컬 상담 시연", lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])


@app.middleware("http")
async def local_only(request: Request, call_next):
    from fastapi.responses import JSONResponse
    if request.client.host not in {"127.0.0.1", "::1"}:
        return JSONResponse({"detail": "로컬 시연 전용입니다."}, status_code=403)
    origin = request.headers.get("origin")
    if origin and origin != f"{request.url.scheme}://{request.headers.get('host')}":
        return JSONResponse({"detail": "다른 사이트에서 접근할 수 없습니다."}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


app.include_router(consultation.router)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
app.include_router(chat.router)
app.include_router(conversation.router)
app.include_router(map_routes.router)
app.include_router(workflow_demo.router)
app.include_router(vending.router)
app.include_router(shop.router)
app.include_router(drug_search.router)
app.include_router(drugs.router)
static_dir = Path(__file__).parent / "static"
app.mount('/kiosk', StaticFiles(directory=static_dir / 'kiosk', html=True), name='kiosk')
app.mount('/static/vendor', StaticFiles(directory=static_dir / 'vendor'), name='vendor')


@app.get('/pharmacist', response_class=HTMLResponse)
def pharmacist_dashboard():
    return (static_dir / 'pharmacist_demo.html').read_text(encoding='utf-8')


@app.get('/scan', response_class=HTMLResponse)
def qr_scanner():
    return (static_dir / 'qr_scan_demo.html').read_text(encoding='utf-8')

# Serve the Flutter build beside the API: credentials stay on the server,
# and the embedded consultation keeps the existing same-origin restriction.
flutter_build = config.BASE_DIR.parent / "moyak-front" / "build" / "web"
if (flutter_build / "index.html").is_file():
    app.mount("/app", StaticFiles(directory=str(flutter_build), html=True), name="flutter")

# Single-process local signaling. Media travels directly between browsers.
signals: dict[str, dict[str, WebSocket]] = {}


@app.websocket("/demo/{cid}/signal")
async def signal(socket: WebSocket, cid: str):
    if socket.client.host not in {"127.0.0.1", "::1"}:
        await socket.close(code=1008)
        return
    origin = socket.headers.get("origin")
    if origin and origin != f"http://{socket.headers.get('host')}":
        await socket.close(code=1008)
        return
    await socket.accept()
    role = None
    try:
        import json
        raw = await asyncio.wait_for(socket.receive_text(), timeout=10)
        if len(raw) > 6000:
            raise ValueError()
        token = json.loads(raw)["token"]
        actor = verify_token(token)
        def validate():
            with SessionLocal() as db:
                _, room = service.participant(db, cid, actor)
                service.active(room)
        await run_in_threadpool(validate)
        peers = signals.setdefault(cid, {})
        if actor.role in peers:
            await socket.close(code=1008, reason="이 역할은 이미 통화 중입니다.")
            return
        role = actor.role
        peers[role] = socket
        if len(peers) == 2:
            for peer in list(peers.values()):
                await peer.send_json({"type": "peer-ready"})
        else:
            await socket.send_json({"type": "waiting"})
        while True:
            try:
                raw = await asyncio.wait_for(socket.receive_text(), timeout=10)
            except asyncio.TimeoutError:
                verify_token(token)
                await run_in_threadpool(validate)
                continue
            verify_token(token)
            await run_in_threadpool(validate)
            if len(raw) > 131072:
                raise ValueError()
            data = json.loads(raw)
            if data.get("type") not in {"offer", "answer", "candidate"}:
                raise ValueError()
            other = peers.get("pharmacist" if role == "user" else "user")
            if other:
                await other.send_json(data)
    except WebSocketDisconnect:
        pass
    except (HTTPException, ValueError, TypeError, KeyError, asyncio.TimeoutError):
        with suppress(RuntimeError):
            await socket.close(code=1008)
    finally:
        peers = signals.get(cid, {})
        if role and peers.get(role) is socket:
            peers.pop(role, None)
            for peer in list(peers.values()):
                with suppress(RuntimeError, WebSocketDisconnect):
                    await peer.send_json({"type": "peer-left"})
            if not peers:
                signals.pop(cid, None)


@app.get("/", response_class=HTMLResponse)
def page():
    return (Path(__file__).parent / "static" / "conversation_demo.html").read_text(encoding="utf-8")


@app.get("/demo-ui.js")
def javascript():
    return Response((Path(__file__).parent / "static" / "conversation_demo.js").read_text(encoding="utf-8"),
                    media_type="text/javascript")


@app.get("/health")
def health():
    return {"status": "ok", "mode": "local-demo", "worker": "integrated"}


@app.get("/demo-rtc.js")
def rtc_javascript():
    return Response((Path(__file__).parent / "static" / "conversation_rtc.js").read_text(encoding="utf-8"),
                    media_type="text/javascript")


@app.post("/demo/start")
def start(request: Request, db: Session = Depends(get_db)):
    if request.headers.get("x-moyak-demo") != "1":
        raise HTTPException(403, "시연 화면에서 시작해주세요.")
    suffix = uuid.uuid4().hex
    user, pharmacist = Actor(f"demo-user-{suffix}", "user"), Actor(f"demo-pharmacist-{suffix}", "pharmacist")
    row = ConsultationRequest(user_id=user.id, chat_summary="로컬 기능 시연용 상담")
    db.add(row)
    db.commit()
    cid = row.id
    service.assign(db, cid, pharmacist)
    return {"id": cid, "user_token": issue_token(user.id, user.role),
            "pharmacist_token": issue_token(pharmacist.id, pharmacist.role),
            "pharmacist_id": pharmacist.id, "ai_available": bool(config.OPENAI_API_KEY),
            "video_available": bool(config.DAILY_API_KEY)}


@app.post("/demo/{cid}/video")
def create_video(cid: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    row, room = service.participant(db, cid, actor, lock=True)
    service.active(room)
    if not row.user_id.startswith("demo-user-"):
        raise HTTPException(403, "시연 상담만 사용할 수 있습니다.")
    if not row.room_url:
        url = video.create_room()
        host = urlparse(url or "").hostname or ""
        if not url or not host.endswith(".daily.co") or urlparse(url).scheme != "https":
            raise HTTPException(502, "Daily 영상방을 생성하지 못했습니다. DAILY_API_KEY를 확인해주세요.")
        row.room_url = url
        db.commit()
    return {"room_url": row.room_url}
