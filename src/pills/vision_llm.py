"""알약 사진을 비전 LLM 에게 보내 모양·색·각인을 **한 번에** 받는다.

지금 방식(`vision.analyze_and_read`)은 검출 → 모양 → 색 → OCR 네 단계가 따로 깨진다.
남은 실패가 정확히 그 자리에 몰려 있다 — 휘어진 각인, 로고 각인, 저대비 알약,
나뭇결·손바닥 배경, 색 판별(77.3%). 비전 LLM 은 알약이 무엇인지 알고 보므로
이 넷을 한 번에 다룰 수 있다.

**기존 코드를 대체하지 않는다.** 같은 119장으로 재서 나을 때만 붙인다
(`scripts/check_pill_search.py --ocr llm`).

모양·색은 **식약처 어휘로 못박는다**(JSON 스키마의 enum). `search.find()` 가
`drug_shape`/`color_class1` 과 글자까지 같은 값을 받아야 조회가 된다.
"""
from __future__ import annotations

import base64
import json
import logging

import cv2
import numpy as np

from src import config
from src.pills.ocr import _clean
# Claude 쪽은 `vision_claude.py` 에 있다. 이 파일의 `_PROMPT`·`_SCHEMA` 를 같이 쓴다.
from src.pills.vision import PillVision

logger = logging.getLogger(__name__)

#: DB `drug_pills.drug_shape` 에 실제로 있는 값 (일반의약품 5,587행 기준).
#: 반원형·육각형은 일반의약품에 0건이라 뺐다 — 고를 수 있으면 고르기도 한다.
SHAPES = ("원형", "타원형", "장방형", "삼각형", "사각형", "마름모형",
          "오각형", "팔각형", "기타")

#: DB `color_class1` 의 첫 색에 실제로 쓰이는 값. "노랑, 투명" 같은 복합값은
#: 첫 색으로 비교하므로(`search.find`) 여기에는 단색만 둔다.
COLORS = ("하양", "노랑", "주황", "분홍", "빨강", "갈색", "연두", "초록", "청록",
          "파랑", "남색", "보라", "자주", "회색", "검정", "투명")

#: 보내기 전에 긴 변을 이만큼으로 줄인다. 토큰(=비용)이 해상도에 비례한다.
MAX_SIDE = 900
#: Claude 연결이 끊겼을 때 다시 보낼 크기. 아래 `ask_llm` 참고.
RETRY_SIDE = 640
#: 사진마다 가운데를 이만큼(가로·세로 비율) 잘라 확대한 사진을 하나 더 보낸다.
#: **폰 사진에서는 알약이 작다** — 화면 폭의 10~20% 인 알약을 900px 로 줄이면 각인이
#: 몇 픽셀이 되어 읽히지 않는다. 사람은 알약을 가운데 두고 찍으므로 가운데를 확대한다.
ZOOM_KEEP = 0.5

_PROMPT = """\
알약 사진이다. 알약의 모양·색·각인을 식약처 낱알식별 기준으로 판정해라.

사진에 대하여
- 같은 약의 앞면과 뒷면이 나란히 찍혀 있을 수 있다. 알약이 둘이면 둘 다 본다.
- 눈금자, 격자 배경, "식품의약품안전처"·"MFDS"·"약학정보원" 같은 출처 표시는 알약이 아니다. 무시한다.
- 배경(책상, 손바닥, 천)의 무늬·그림자를 알약으로 착각하지 않는다.
- 사진마다 바로 뒤에 그 사진의 **가운데를 확대한 사진**이 붙어 있다. 각인은 확대 사진에서
  더 잘 보인다. 확대 사진에 알약이 없거나 잘려 있으면 원래 사진으로 판단한다.

shape — 알약 윤곽
- 원형: 동그랗다
- 타원형: 길쭉하고 윤곽이 매끈한 타원
- 장방형: 길쭉하고 양 끝이 둥근 직사각형(캡슐 모양). 경질·연질 캡슐도 장방형이다
- 삼각형/사각형/마름모형/오각형/팔각형: 각진 모양
- 기타: 위 어디에도 안 맞을 때만

color — 알약 본체의 색. 두 색이면 더 넓은 쪽. 조명 때문에 생긴 색조는 빼고 판단한다.
흰 알약이 그늘에서 회색으로 보여도 하양이다.

imprint — 알약 표면에 새겨지거나 인쇄된 **글자와 숫자**를 보이는 그대로
- 알약이 둘이면 각 알약의 글자를 공백으로 이어 쓴다 (예: 앞 "D-W", 뒤 "MPG" → "D-W MPG")
- 한 알약 안에서 줄이 나뉘거나 분할선 양쪽에 있으면 공백으로 구분한다 (예: "A C")
- 원을 따라 둥글게 새겨진 글자도 읽는다
- 영문은 대문자로. 한글 각인이면 한글 그대로. 하이픈은 남긴다
- 로고·심볼·분할선은 글자로 옮기지 않는다. 회사 로고는 글자처럼 생긴 것이 많다
  (모양을 꾸민 D, 겹친 글자, 원 안의 기호). 보통 글꼴의 글자·숫자만 적고,
  꾸민 기호 하나가 글자인지 로고인지 애매하면 imprint 에서 빼고 has_logo 를 true 로 한다
- 흐릿하면 가장 그럴듯한 글자로 적는다. 글자가 전혀 없으면 null

has_logo — 글자가 아닌 회사 로고·심볼이 새겨져 있으면 true
split_line — 쪼개는 홈(분할선)이 있으면 true
aspect — 알약 하나의 긴 변 ÷ 짧은 변 (원형은 1.0 근처)
confidence — 판정 전체에 대한 확신 0~1. 알약이 안 보이거나 흐리면 낮게
kinds — 사진 속 서로 다른 약의 종류 수. 같은 약이 여러 알이면 1
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "shape": {"type": "string", "enum": list(SHAPES)},
        "color": {"type": "string", "enum": list(COLORS)},
        "imprint": {"anyOf": [{"type": "string"}, {"type": "null"}]},
        "has_logo": {"type": "boolean"},
        "split_line": {"type": "boolean"},
        "aspect": {"type": "number"},
        "confidence": {"type": "number"},
        "kinds": {"type": "integer"},
    },
    "required": ["shape", "color", "imprint", "has_logo", "split_line",
                 "aspect", "confidence", "kinds"],
    "additionalProperties": False,
}


class LLMError(RuntimeError):
    """LLM 을 부를 수 없었다(키 없음·네트워크·응답 이상). 호출한 쪽이 기존 방식으로 떨어진다."""


def _shrink(image_bytes: bytes, max_side: int, quality: int = 92) -> bytes:
    """긴 변을 `max_side` 로 줄여 JPEG 로 돌려준다."""
    bgr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("이미지를 읽을 수 없습니다. JPEG/PNG 인지 확인하세요.")
    long_side = max(bgr.shape[:2])
    if long_side > max_side:
        s = max_side / long_side
        bgr = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("이미지를 인코딩하지 못했습니다.")
    return buf.tobytes()


#: 사진을 여러 장(앞면·뒷면) 보낼 때 질문 앞에 붙인다.
_MULTI = ("원래 사진 {n}장(각각 바로 뒤에 확대 사진이 붙어 있다)은 **같은 알약**을 여러 면에서 찍은 것이다(예: 앞면과 뒷면). "
          "모든 사진을 함께 보고 하나의 판정을 내려라. imprint 에는 모든 면의 글자를 공백으로 이어 적는다."
          "\n\n")


def _center(image_bytes: bytes, keep: float) -> bytes:
    """가운데를 `keep` 비율만큼 잘라 원래 해상도 그대로 돌려준다(확대는 `_shrink` 가 맞춘다)."""
    bgr = cv2.imdecode(np.frombuffer(image_bytes, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("이미지를 읽을 수 없습니다. JPEG/PNG 인지 확인하세요.")
    h, w = bgr.shape[:2]
    ch, cw = int(h * keep), int(w * keep)
    y0, x0 = (h - ch) // 2, (w - cw) // 2
    ok, buf = cv2.imencode(".jpg", bgr[y0:y0 + ch, x0:x0 + cw], [cv2.IMWRITE_JPEG_QUALITY, 95])
    return buf.tobytes()


def _prepare(images: list[bytes], side: int, quality: int = 92) -> list[bytes]:
    """사진마다 [원래 사진, 가운데 확대] 를 만든다."""
    out = []
    for b in images:
        out.append(_shrink(b, side, quality))
        if ZOOM_KEEP:
            out.append(_shrink(_center(b, ZOOM_KEEP), side, quality))
    return out


def _as_list(images: bytes | list[bytes]) -> list[bytes]:
    return [images] if isinstance(images, (bytes, bytearray)) else list(images)


def ask_llm(images: bytes | list[bytes], model: str | None = None,
            max_side: int = MAX_SIDE) -> dict:
    """사진(한 장, 또는 앞뒤 여러 장)을 보내 판정 JSON 을 받는다. 사용량(`usage`)도 담는다.

    채점 스크립트가 이 dict 를 그대로 캐시에 넣는다 — 점수·검색만 고칠 때 LLM 을
    다시 부르지 않으려는 것이다.
    """
    model = model or config.PILL_LLM_MODEL
    images = _as_list(images)
    jpgs = _prepare(images, max_side)
    prompt = (_MULTI.format(n=len(images)) if len(images) > 1 else "") + _PROMPT

    # Claude 는 SDK 가 달라 따로 부른다. 질문과 스키마는 같은 것을 넘긴다.
    if model.startswith("claude"):
        from src.pills.vision_claude import ask_claude
        try:
            try:
                return ask_claude(jpgs, model, prompt, _SCHEMA)
            except ConnectionError:
                # **큰 요청일수록 연결이 끊긴다** (2026-10-19, 이 PC 에서 측정). 같은 사진을
                # 30KB 로 보내면 4/4, 129KB 면 1/4 성공했고, 글자만 보내면 6/6 이었다.
                # SDK 가 아니라 네트워크 쪽 문제다 — requests 로 보내도 똑같이 끊겼다.
                # 그래서 끊기면 사진을 줄여 한 번 더 보낸다. 그래도 안 되면 예전 방식으로.
                small = _prepare(images, min(max_side, RETRY_SIDE), quality=85)
                return ask_claude(small, model, prompt, _SCHEMA)
        except Exception as exc:
            logger.warning("비전 LLM 호출 실패: %s", type(exc).__name__)
            raise LLMError(str(exc) if isinstance(exc, (RuntimeError, ConnectionError)) else
                           type(exc).__name__) from exc

    # 알약 전용 키가 있으면 그걸 쓴다. 임베딩 키와 사용량·요금을 나눠 보려는 것이다.
    key = config.PILL_OPENAI_API_KEY or config.OPENAI_API_KEY
    if not key:
        raise LLMError("OPENAI_API_KEY 가 없습니다.")

    from openai import OpenAI     # 키가 없을 때는 import 조차 하지 않는다

    req: dict = {
        "model": model,
        "input": [{
            "role": "user",
            "content": [{"type": "input_text", "text": prompt}] + [
                {"type": "input_image", "detail": "high",
                 "image_url": "data:image/jpeg;base64," + base64.b64encode(j).decode()}
                for j in jpgs
            ],
        }],
        "text": {"format": {"type": "json_schema", "name": "pill",
                            "schema": _SCHEMA, "strict": True}},
    }
    # 추론 모델은 기본값으로 두면 오래 생각하느라 느리고 비싸다. 보고 옮기는 일이라
    # 깊은 추론이 필요 없다.
    if model.startswith(("gpt-5", "o")):
        req["reasoning"] = {"effort": "low"}

    try:
        res = OpenAI(api_key=key, timeout=60, max_retries=2).responses.create(**req)
        data = json.loads(res.output_text)
    except Exception as exc:
        # 예외 문자열에 요청 내용이 섞일 수 있어 종류만 남긴다(ocr.py 와 같은 이유).
        logger.warning("비전 LLM 호출 실패: %s", type(exc).__name__)
        raise LLMError(type(exc).__name__) from exc

    u = getattr(res, "usage", None)
    data["usage"] = {"model": model,
                     "input": getattr(u, "input_tokens", 0),
                     "output": getattr(u, "output_tokens", 0)}
    return data


def to_vision(data: dict, aspect: float | None = None) -> tuple[PillVision, str | None]:
    """`ask_llm` 의 결과를 기존 파이프라인이 쓰는 모양으로 바꾼다.

    `aspect` 를 주면 LLM 의 눈대중 대신 그 값을 쓴다(OpenCV 로 잰 값).
    """
    shape = data.get("shape") if data.get("shape") in SHAPES else "기타"
    color = data.get("color") if data.get("color") in COLORS else "하양"
    # 각인은 OCR 과 **같은 거름망**을 거친다. 'MFDS' 같은 출처 표시를 읽었거나
    # 소문자로 줬을 때 검색 쪽(`search._tokens`)과 기준이 어긋나지 않게 한다.
    imprint = _clean(data.get("imprint") or "") or None
    try:
        conf = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except (TypeError, ValueError):
        conf = 0.0
    if aspect is None:
        try:
            aspect = float(data.get("aspect")) or None
        except (TypeError, ValueError):
            aspect = None
    vision = PillVision(
        shape=shape, color=color, confidence=round(conf, 3), aspect=aspect,
        detail={"engine": "llm", "has_logo": bool(data.get("has_logo")),
                "split_line": bool(data.get("split_line")), "llm": data},
    )
    return vision, imprint


def analyze_llm(images: bytes | list[bytes],
                model: str | None = None) -> tuple[PillVision, str | None]:
    """사진(앞뒤 여러 장도 된다)을 비전 LLM 에게 보내 모양·색·각인을 받는다.

    실패하면 `LLMError` 를 낸다 — 호출한 쪽에서 기존 방식으로 떨어지면 된다.
    """
    return to_vision(ask_llm(images, model=model))


def analyze_best(images: bytes | list[bytes],
                 llm: bool = True) -> tuple[PillVision, str | None]:
    """API 가 쓰는 입구. **LLM 을 먼저 쓰고, 안 되면 예전 방식으로 떨어진다.**

    LLM 이 안 되는 경우 — 모델 설정이 비었다, 키가 없다, 네트워크·요금·거절 — 에는
    OpenCV + Google Vision(`vision.analyze_and_read`)으로 같은 모양의 결과를 낸다.
    어느 쪽이 답했는지는 `PillVision.detail["engine"]` 으로 알 수 있다.

    가로세로비는 LLM 의 눈대중을 그대로 쓴다. OpenCV 로 잰 값과 119장 결과가 거의
    같았고(5위 안 91.6% vs 90.8%), OpenCV 는 그늘진 배경에서 그림자를 재기 때문이다.
    """
    images = _as_list(images)
    # llm=False 는 하루 비용 상한을 넘었을 때다(`identify.DailyBudget`).
    if llm and config.PILL_LLM_MODEL:
        try:
            return analyze_llm(images)
        except LLMError:
            pass        # 원인은 ask_llm 이 이미 로그에 남겼다
    from src.pills.vision import analyze_and_read
    # 예전 방식은 한 장씩 읽는다. 모양·색은 첫 장, 각인은 모든 장에서 읽은 것을 합친다.
    seen, imprint = analyze_and_read(images[0])
    words = (imprint or "").split()
    for raw in images[1:]:
        for w in (analyze_and_read(raw)[1] or "").split():
            if w not in words:
                words.append(w)
    seen.detail.setdefault("engine", "opencv")
    return seen, " ".join(words) or None
