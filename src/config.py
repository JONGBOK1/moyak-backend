from pathlib import Path

from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# e약은요 API
EYAK_SERVICE_KEY = os.getenv("EYAK_SERVICE_KEY")
EYAK_BASE_URL = "https://apis.data.go.kr/1471000/DrbEasyDrugInfoService/getDrbEasyDrugList"

# 이후 STEP 3~6에서 사용
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
PINECONE_API_KEY = os.getenv("PINECONE_API_KEY")

# 화상 상담용 Daily.co
DAILY_API_KEY = os.getenv("DAILY_API_KEY")

# 데이터 경로
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
DATA_PROCESSED_DIR = BASE_DIR / "data" / "processed"

# 상담/자판기 연동용 DB. 기본값은 로컬 SQLite 파일.
# 배포 환경에서 재시작 시에도 기록을 남기려면 DATABASE_URL을 Postgres 등으로 지정할 것
# (Render free 인스턴스는 디스크가 휘발성이라 SQLite 파일은 재배포/재시작 시 초기화됨).
# 지도 화면의 기본 중심 좌표 "위도,경도" — 위치 권한이 없을 때 이 위치를 보여준다. 기본값: 동양미래대학교
DEMO_MAP_CENTER = os.getenv("DEMO_MAP_CENTER", "37.5011,126.8670")

# 카카오 지도 JavaScript 키 (developers.kakao.com > 내 애플리케이션 > 앱 키).
# 웹페이지에 노출되는 공개 키이고 등록한 도메인에서만 동작한다 — 그래도 코드에 직접 쓰지 않고 .env로만 관리.
KAKAO_JS_KEY = os.getenv("KAKAO_JS_KEY")

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'moyak.db'}")

if not EYAK_SERVICE_KEY:
    raise RuntimeError("EYAK_SERVICE_KEY가 .env에 설정되어 있지 않습니다.")