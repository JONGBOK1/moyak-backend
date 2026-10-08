"""Run the ordinary application locally (상담·구매 기록은 로컬 SQLite, Supabase는 읽기 전용).

    python scripts/run_unified_local.py              # 이 PC 브라우저 전용 시연
    python scripts/run_unified_local.py --flutter    # Flutter 개발용: 폰·에뮬레이터·Flutter 웹 접속 허용
    python scripts/run_unified_local.py --local-map  # 지도도 로컬 가상 자판기(M001 등)로 표시
"""
import os
import socket
import sys
from pathlib import Path
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def lan_address() -> str | None:
    """같은 와이파이의 폰이 접속할 이 PC의 주소 (실제 패킷은 보내지 않음)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        try:
            probe.connect(('8.8.8.8', 80))
            return probe.getsockname()[0]
        except OSError:
            return None


if __name__ == '__main__':
    flutter = '--flutter' in sys.argv
    port = int(sys.argv[sys.argv.index('--port') + 1]) if '--port' in sys.argv else 8000
    values = dotenv_values(ROOT / '.env')
    catalog = os.getenv('DATABASE_URL') or values.get('DATABASE_URL')
    if catalog:
        os.environ['CATALOG_DATABASE_URL'] = catalog
        os.environ['MAP_DATABASE_URL'] = catalog
    # An explicit local map override keeps M001 and kiosk demo inventory together.
    if '--local-map' in sys.argv:
        os.environ['MAP_DATABASE_URL'] = f"sqlite:///{ROOT / 'data' / 'unified-local.db'}"
    (ROOT / 'data').mkdir(exist_ok=True)
    os.environ['DATABASE_URL'] = f"sqlite:///{ROOT / 'data' / 'unified-local.db'}"
    os.environ['MOYAK_LOCAL_DEMO'] = '1'
    if flutter:
        # 신원은 팀 방식(요청의 ID·역할)으로 처리되므로 '이 PC 전용' 경계는 끈다.
        os.environ.pop('MOYAK_LOCAL_IDENTITY', None)
        os.environ.setdefault('CORS_ORIGIN_REGEX', r'https?://(localhost|127\.0\.0\.1)(:\d+)?')
        host = '0.0.0.0'
        ip = lan_address()
        print('Flutter 개발 모드 — 같은 와이파이에서만 사용하세요 (인증 없음).')
        print(f'  크롬/윈도우:     http://127.0.0.1:{port}')
        print(f'  안드로이드 에뮬: http://10.0.2.2:{port}')
        if ip:
            print(f'  실제 폰:         http://{ip}:{port}  (Windows 방화벽에서 Python 허용 필요)')
    else:
        os.environ['MOYAK_LOCAL_IDENTITY'] = '1'
        host = '127.0.0.1'
    import uvicorn
    uvicorn.run('src.api.main:app', host=host, port=port, proxy_headers=False)
