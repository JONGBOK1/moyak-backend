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

# 로그인 서버에서만 발급하는 상담용 서명 토큰. 미설정 시 새 상담 API는 비활성화된다.
CONSULT_AUTH_SECRET = os.getenv("CONSULT_AUTH_SECRET", "")
CONSULT_TRANSCRIPTION_MODEL = os.getenv("CONSULT_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
CONSULT_SUMMARY_MODEL = os.getenv("CONSULT_SUMMARY_MODEL", "gpt-4o")
KAKAO_JS_KEY = os.getenv("KAKAO_JS_KEY", "")
DEMO_MAP_CENTER = os.getenv("DEMO_MAP_CENTER", "37.5011,126.8670")
KAKAO_JS_KEY = os.getenv("KAKAO_JS_KEY", "")
DEMO_MAP_CENTER = os.getenv("DEMO_MAP_CENTER", "37.5011,126.8670")

# 데이터 경로
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
DATA_PROCESSED_DIR = BASE_DIR / "data" / "processed"

# 상담/자판기 연동용 DB. 기본값은 로컬 SQLite 파일.
# 배포 환경에서 재시작 시에도 기록을 남기려면 DATABASE_URL을 Postgres 등으로 지정할 것
# (Render free 인스턴스는 디스크가 휘발성이라 SQLite 파일은 재배포/재시작 시 초기화됨).
DATABASE_URL = os.getenv("DATABASE_URL") or f"sqlite:///{BASE_DIR / 'data' / 'moyak.db'}"

if not EYAK_SERVICE_KEY:
    raise RuntimeError("EYAK_SERVICE_KEY가 .env에 설정되어 있지 않습니다.")
