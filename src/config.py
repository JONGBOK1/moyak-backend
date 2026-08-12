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

# 데이터 경로
DATA_RAW_DIR = BASE_DIR / "data" / "raw"
DATA_PROCESSED_DIR = BASE_DIR / "data" / "processed"

if not EYAK_SERVICE_KEY:
    raise RuntimeError("EYAK_SERVICE_KEY가 .env에 설정되어 있지 않습니다.")