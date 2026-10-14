"""알약 각인을 글자로 읽는다.

**왜 이게 제일 중요한가** — 실측(`scripts/check_pill_search.py`, 119장):

    모양+색만      후보에 정답 9.2%  · 1위 정답  0.0%  · 평균 후보 30개(잘림)
    모양+색+각인   후보에 정답 91.6% · 1위 정답 78.2% · 평균 후보 4.9개

각인이 읽히느냐 아니냐로 기능이 되고 안 되고가 갈린다. 모양·색은 거들 뿐이다.

Google Cloud Vision 을 쓴다. 알약 각인은 작고 음각이라 흔한 오픈소스 OCR 로는
잘 안 읽히고, 상용 엔진이 눈에 띄게 낫다. 월 1,000건까지 무료라 발표용으로는 비용이 없다.

키가 없으면 **조용히 None 을 돌려준다**. 각인 없이 모양·색만으로도 후보는 나오므로
기능 전체가 멈추지는 않게 한다.
"""
from __future__ import annotations

import base64
import logging
import re

import cv2
import numpy as np
import requests

from src import config

logger = logging.getLogger(__name__)

_ENDPOINT = "https://vision.googleapis.com/v1/images:annotate"

#: 각인에 실제로 쓰이는 글자만 남긴다.
#: 영문 대문자·숫자·기호에 더해 **한글**도 남긴다 — 실측상 일반의약품 각인의
#: 18.0%(955/5,297)가 한글이다("깅코탄", "제로민" 등). 영어만 읽으면 그만큼 통째로 놓친다.
_ALLOWED = re.compile(r"[^A-Z0-9가-힣/\-+.]")

#: 식약처 사진에 같이 찍힌 글자들. 각인으로 오인하면 안 된다.
#: 식약처 사진에 같이 찍힌 글자와, 각인 자리에 쓰이지만 **글자가 아닌 표시**.
#: "마크"(회사 로고)가 2,600건, "분할선"(쪼개는 홈)이 2,032건 있다. OCR 로 읽을 수 없고
#: 읽으려 들면 엉뚱한 글자가 나오므로 양쪽에서 똑같이 걸러야 비교가 맞는다.
_NOISE = {"MFDS", "MINISTRY", "OF", "FOOD", "AND", "DRUG", "SAFETY",
          "식품의약품안전처", "마크", "분할선"}


def _preprocess(crop: np.ndarray) -> bytes:
    """각인이 잘 보이도록 다듬는다.

    **이 방식이 최선이다.** 다른 6가지를 같은 사진 25장으로 재봤는데 전부 더 나빴다
    (`scripts/tune_pill_ocr.py`):

        현재 (CLAHE)        44.0%   ← 최고
        더 크게 확대         20.0%
        언샤프 마스킹        28.0%
        모폴로지 블랙햇       0.0%
        적응형 이진화         4.0%
        언샤프+블랙햇         0.0%
        현재+언샤프 둘 다 읽기 40.0%  (시간은 3배)

    "대비를 더 세게 올리면 낫겠지" 같은 직관이 여기서는 전부 틀렸다.
    고치기 전에 반드시 위 스크립트로 재 볼 것.

    각인은 음각이라 **빛이 비치는 쪽과 그늘 쪽의 대비**로 보인다.
    색은 방해만 되므로 흑백으로 바꾸고, 국소 대비를 올린다(CLAHE).
    너무 작으면 OCR 이 글자를 못 잡으므로 짧은 변을 400px 이상으로 키운다.
    """
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

    short = min(gray.shape[:2])
    if short < 400 and short > 0:
        scale = 400 / short
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    # 알약 표면은 가운데가 밝고 가장자리가 어두워서 전체 대비 조정으로는 부족하다.
    # CLAHE 는 영역을 나눠 각각 대비를 올려서 각인이 고르게 드러난다.
    gray = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)

    ok, buf = cv2.imencode(".png", gray)
    if not ok:
        raise ValueError("전처리한 이미지를 인코딩하지 못했습니다.")
    return buf.tobytes()


def _clean(raw: str) -> str:
    """OCR 이 뱉은 글자에서 각인만 남긴다."""
    out = []
    for token in raw.replace("\n", " ").split():
        t = _ALLOWED.sub("", token.upper())
        if not t or t in _NOISE:
            continue
        # 한 글자짜리 숫자는 그림자나 얼룩일 때가 많아 버린다. 영문·한글 한 글자는 남긴다.
        if len(t) == 1 and t.isdigit():
            continue
        out.append(t)
    return " ".join(out)


#: EasyOCR 모델은 한 번 만들면 재사용한다. 만들 때마다 100MB 모델을 다시 읽으면 느리다.
_easy_reader = None


def _read_easyocr(crop: np.ndarray) -> str | None:
    """오픈소스 OCR. 키도 비용도 없지만 작은 음각 글자에 약하다.

    각인은 영문 대문자·숫자뿐이라 언어를 영어로 못박는다.
    한국어를 같이 넣으면 엉뚱한 한글을 만들어낸다.
    """
    global _easy_reader
    try:
        import easyocr
    except ImportError:
        logger.info("easyocr 가 설치돼 있지 않습니다.")
        return None

    if _easy_reader is None:
        # 처음 한 번은 모델(약 100MB)을 받느라 오래 걸린다.
        #
        # **영어만 쓴다.** 각인의 18% 가 한글이라 한글 모델을 같이 넣어 봤는데,
        # 한글을 넣으려면 allowlist 를 포기해야 하고 그 순간 영문 각인이 무너졌다:
        #   'H54' → '발 방' · 'NAP' → 'IVII' · 'THROAKOOL' → 'XHA 00 자'
        # 실측 정확도 62.2% → 21.6%. 한글 18% 를 얻으려다 영문 82% 를 잃는 거래였다.
        _easy_reader = easyocr.Reader(["en"], gpu=False, verbose=False)

    try:
        png = _preprocess(crop)
        arr = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_GRAYSCALE)
        # allowlist 가 정확도의 핵심이다. 각인에 안 쓰이는 글자를 애초에 못 내놓게 막는다.
        parts = _easy_reader.readtext(
            arr, detail=0, paragraph=False,
            allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-+.",
        )
    except Exception as exc:
        logger.warning("easyocr 실패: %s", type(exc).__name__)
        return None

    return _clean(" ".join(parts)) or None


def read_many(crops: list, engine: str = "auto") -> str | None:
    """조각 여러 개를 읽어 합친다.

    식약처 사진은 같은 약을 앞뒤로 두 개 찍어 놓아서 각인이 서로 다르다
    (예: 앞면 'D-W', 뒷면 'MPG'). 하나만 읽으면 절반을 놓친다.
    실측상 25장 전부 조각이 2개 이상 잡혔다.

    검색은 어느 쪽 각인이든 맞으면 되므로 읽은 것을 전부 이어 붙여 넘긴다.
    """
    seen: list[str] = []
    for c in crops[:2]:          # 셋째부터는 먼지일 가능성이 커서 읽지 않는다
        if c is None or getattr(c, "size", 0) == 0:
            continue
        got = read_imprint(c, engine=engine)
        for token in (got or "").split():
            if token not in seen:
                seen.append(token)
    return " ".join(seen) or None


def read_imprint(crop: np.ndarray, engine: str = "auto") -> str | None:
    """알약 조각 **하나**에서 각인을 읽는다. 실패하면 None.

    engine:
      "auto"     Google 키가 있으면 Google, 없으면 easyocr
      "google"   Google Cloud Vision 만
      "easyocr"  오픈소스만 (비교 측정용)
    """
    if engine == "easyocr":
        return _read_easyocr(crop)

    key = (config.GOOGLE_VISION_API_KEY or "").strip()
    if not key:
        if engine == "google":
            logger.info("GOOGLE_VISION_API_KEY 가 없습니다.")
            return None
        return _read_easyocr(crop)

    try:
        png = _preprocess(crop)
    except Exception as exc:
        logger.warning("각인 전처리 실패: %s", exc)
        return None

    body = {
        "requests": [{
            "image": {"content": base64.b64encode(png).decode()},
            # TEXT_DETECTION 은 흩어진 짧은 글자에 강하다.
            # DOCUMENT_TEXT_DETECTION 은 문서용이라 각인에는 안 맞는다.
            "features": [{"type": "TEXT_DETECTION", "maxResults": 1}],
            "imageContext": {"languageHints": ["en"]},
        }]
    }
    try:
        res = requests.post(_ENDPOINT, params={"key": key}, json=body, timeout=15)
        if res.status_code != 200:
            # requests 의 예외 메시지에는 **키가 붙은 URL 이 통째로** 들어간다.
            # 그대로 로그에 남기면 비밀값이 파일과 화면에 찍히므로 직접 꺼내 쓴다.
            detail = ""
            try:
                detail = res.json().get("error", {}).get("message", "")[:200]
            except Exception:
                pass
            logger.warning("Vision API 응답 %s: %s", res.status_code, detail)
            # 결제 미반영(403) 같은 일시적 문제면 오픈소스로라도 읽어 본다.
            return _read_easyocr(crop) if engine == "auto" else None
        data = res.json()["responses"][0]
    except Exception as exc:
        # 예외 문자열에도 URL 이 섞일 수 있어 종류만 남긴다.
        logger.warning("Vision API 호출 실패: %s", type(exc).__name__)
        return None

    if "error" in data:
        logger.warning("Vision API 오류: %s", data["error"].get("message", "")[:200])
        return None

    raw = (data.get("fullTextAnnotation") or {}).get("text", "")
    cleaned = _clean(raw)
    return cleaned or None
