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
# 상담 음성 전사·요약 (src/consult/ai.py) — 한별 구현 이식
CONSULT_TRANSCRIPTION_MODEL = os.getenv("CONSULT_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe")
CONSULT_SUMMARY_MODEL = os.getenv("CONSULT_SUMMARY_MODEL", "gpt-4o")
# Bearer 토큰 서명용(선택, 32자 이상). 비어 있으면 토큰 대신 X-Moyak-User-Id/X-Moyak-Role 헤더(팀 방식)만 사용
CONSULT_AUTH_SECRET = os.getenv("CONSULT_AUTH_SECRET", "")

# 앱 로그인 (Supabase Auth) — 팀 users.id가 auth.users를 참조하므로 계정은 Supabase Auth에 만든다.
# 프로젝트 URL·anon 키·service_role 키는 Supabase 대시보드 > Project Settings > API.
# service_role 키는 서버 전용 비밀값(앱·웹페이지에 절대 넣지 않음).
# 키·주소에는 공백이 있을 수 없으므로, 대시보드에 붙여넣을 때 섞인 줄바꿈·공백을 모두 제거한다
# (Render에서 긴 키에 줄바꿈이 끼어 HTTP 헤더 오류로 로그인이 503이 났던 문제 방지)
def _compact(name: str) -> str:
    return "".join((os.getenv(name) or "").split())


SUPABASE_URL = _compact("SUPABASE_URL").rstrip("/")
SUPABASE_ANON_KEY = _compact("SUPABASE_ANON_KEY")
SUPABASE_SERVICE_ROLE_KEY = _compact("SUPABASE_SERVICE_ROLE_KEY")
# 앱은 '아이디'로 가입하지만 Supabase Auth는 이메일이 필요해 내부적으로 "아이디@도메인"으로 변환한다(메일 발송 없음).
AUTH_EMAIL_DOMAIN = (os.getenv("AUTH_EMAIL_DOMAIN") or "").strip() or "moyak.app"
# 로그인 토큰 유효기간(초). 기본 14일 — 만료되면 앱에서 다시 로그인.
AUTH_TOKEN_TTL_SECONDS = int((os.getenv("AUTH_TOKEN_TTL_SECONDS") or "").strip() or 14 * 86400)

# 지도 화면의 기본 중심 좌표 "위도,경도" — 위치 권한이 없을 때 이 위치를 보여준다. 기본값: 동양미래대학교
DEMO_MAP_CENTER = os.getenv("DEMO_MAP_CENTER", "37.5011,126.8670")

# 카카오 지도 JavaScript 키 (developers.kakao.com > 내 애플리케이션 > 앱 키).
# 웹페이지에 노출되는 공개 키이고 등록한 도메인에서만 동작한다 — 그래도 코드에 직접 쓰지 않고 .env로만 관리.
KAKAO_JS_KEY = os.getenv("KAKAO_JS_KEY")

DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'moyak.db'}")

if not EYAK_SERVICE_KEY:
    raise RuntimeError("EYAK_SERVICE_KEY가 .env에 설정되어 있지 않습니다.")