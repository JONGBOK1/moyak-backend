"""후보들의 식약처 사진을 Claude 에게 보여주고 **다시 고르게** 한다.

검색은 글자로만 비교한다. 그래서 이런 것을 못 가른다:

  - 로고만 있는 약끼리 — 삼각형·파랑·로고인 약이 3개면 셋이 동점이다(아셀렉스정)
  - 짧은 각인 — '50' 은 수십 개 약에 똑같이 있다(쿠티아핀정50)
  - 일부만 읽은 각인 — 'D CT' 의 D 가 사실은 로고다(디카테오)

사람이라면 후보 사진을 나란히 놓고 "이거랑 똑같네" 하고 고른다. 그걸 시킨다.
1위가 확실할 때(각인이 통째로 맞고 2위와 벌어짐)는 부르지 않는다(`identify.is_decisive`).

후보 사진은 **한 장으로 모아** 보낸다. 사진을 10장 따로 보내면 요청이 커지는데, 이 PC 에서는
큰 요청일수록 연결이 끊긴다(`vision_llm.ask_llm` 참고). Claude 가 식약처 사진 주소를 직접
받아 오게 하는 것도 해 봤지만 식약처 서버에서 시간 초과가 났다.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
import requests

from src import config
from src.pills.search import Candidate
from src.pills.vision import PillVision, _find_pill
from src.pills.vision_llm import _center, _shrink

logger = logging.getLogger(__name__)

#: 식약처 사진을 받아 두는 곳. 채점 스크립트와 같은 폴더를 쓴다.
REF_DIR = Path(__file__).resolve().parents[2] / "data" / "pill_cache"
CELL_W, CELL_H, COLS = 360, 180, 2

#: 사용자 사진이 한 장일 때와 두 장일 때 설명이 달라야 한다. 앞면만 보내면서
#: "반대쪽 면" 이라고 하면 Claude 가 없는 사진을 찾는다.
_HEAD_ONE = "앞의 사진 2장은 사용자가 찍은 알약이다(두 번째는 첫 사진의 가운데를 확대한 것)."
_HEAD_TWO = ("앞의 사진 3장은 사용자가 찍은 알약이다 — 첫 장이 한쪽 면, 두 번째는 그 가운데를 "
             "확대한 것, 세 번째는 **같은 알약의 반대쪽 면**이다.")

_PROMPT = """\
{head}
마지막 사진은 후보 약들의 식약처 공식 사진을 모은 것이다. 칸마다 왼쪽 위에 번호가 있고,
한 칸에 같은 약의 앞면과 뒷면이 함께 있다.

후보별 DB 정보:
{table}

사용자 알약과 **같은 약**일 가능성이 높은 순서로 후보 번호를 나열해라.
각인 글자, 로고 모양, 분할선, 모양, 색을 비교한다. 사용자 사진은 조명 때문에 색이 달라 보일 수
있다. 확실히 다른 후보는 빼도 된다.
"""

_SCHEMA = {
    "type": "object",
    "properties": {
        "order": {"type": "array", "items": {"type": "integer"}},
        "confidence": {"type": "number"},
    },
    "required": ["order", "confidence"],
    "additionalProperties": False,
}


def _ref_image(c: Candidate) -> np.ndarray | None:
    """후보의 식약처 사진에서 알약 부분만 잘라 온다. 처음 한 번은 받아서 저장한다."""
    path = REF_DIR / f"{c.item_seq}.jpg"
    raw = path.read_bytes() if path.exists() else None
    if raw is None and c.item_image:
        try:
            res = requests.get(c.item_image, timeout=8)
            res.raise_for_status()
            raw = res.content
            REF_DIR.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        except Exception as exc:
            logger.info("후보 사진 못 받음 %s: %s", c.item_seq, type(exc).__name__)
            return None
    if not raw:
        return None
    bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        return None
    s = 900 / max(bgr.shape[:2])
    if s < 1:
        bgr = cv2.resize(bgr, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
    # 눈금자·로고를 빼고 알약 두 개(앞·뒤)만 남긴다. 식약처 사진은 배경이 고르게
    # 파래서 기존 검출이 잘 된다(모양 88%).
    cnts = _find_pill(bgr)[:2]
    # **앞뒤 두 알이 비슷한 크기로 잡혔을 때만 자른다.** 알약을 크게 찍은 식약처 사진은
    # 검출이 엉뚱한 귀퉁이를 잡아서, 잘라 내면 알약이 반쯤 날아갔다. 그럴 땐 통째로 쓴다.
    areas = [cv2.contourArea(k) for k in cnts]
    if len(cnts) == 2 and min(areas) / max(areas) > 0.6:
        xs, ys = [], []
        for k in cnts:
            x, y, w, h = cv2.boundingRect(k)
            xs += [x, x + w]
            ys += [y, y + h]
        pad = int(0.08 * (max(xs) - min(xs)))
        x0, x1 = max(0, min(xs) - pad), min(bgr.shape[1], max(xs) + pad)
        y0, y1 = max(0, min(ys) - pad), min(bgr.shape[0], max(ys) + pad)
        return bgr[y0:y1, x0:x1]
    h, w = bgr.shape[:2]
    return bgr[int(h * .15):int(h * .9), int(w * .05):int(w * .95)]


def make_sheet(cands: list[Candidate]) -> bytes:
    """후보 사진을 번호를 붙여 한 장으로 모은다 (2열)."""
    with ThreadPoolExecutor(max_workers=5) as pool:
        refs = list(pool.map(_ref_image, cands))
    rows = (len(cands) + COLS - 1) // COLS
    sheet = np.full((rows * CELL_H, COLS * CELL_W, 3), 255, np.uint8)
    for i, ref in enumerate(refs):
        r, col = divmod(i, COLS)
        y, x = r * CELL_H, col * CELL_W
        if ref is not None and ref.size:
            s = min((CELL_W - 8) / ref.shape[1], (CELL_H - 8) / ref.shape[0])
            im = cv2.resize(ref, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
            oy, ox = y + (CELL_H - im.shape[0]) // 2, x + (CELL_W - im.shape[1]) // 2
            sheet[oy:oy + im.shape[0], ox:ox + im.shape[1]] = im
        cv2.rectangle(sheet, (x, y), (x + CELL_W - 1, y + CELL_H - 1), (160, 160, 160), 1)
        cv2.rectangle(sheet, (x + 2, y + 2), (x + 40, y + 30), (0, 0, 0), -1)
        cv2.putText(sheet, str(i + 1), (x + 6, y + 25), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (255, 255, 255), 2, cv2.LINE_AA)
    ok, buf = cv2.imencode(".jpg", sheet, [cv2.IMWRITE_JPEG_QUALITY, 82])
    return buf.tobytes()


def _table(cands: list[Candidate]) -> str:
    return "\n".join(
        f"{i + 1}. 모양 {c.drug_shape or '-'} · 색 {c.color_class1 or '-'} · "
        f"각인 앞 '{c.print_front or '-'}' 뒤 '{c.print_back or '-'}'"
        for i, c in enumerate(cands))


def _user_images(images: list[bytes], width: int, quality: int) -> list[bytes]:
    """재순위에 보낼 사용자 사진: 앞면 + 앞면 가운데 확대 + (있으면) 뒷면.

    **뒷면을 빼면 안 된다.** 후보 모음 사진은 칸마다 앞·뒤가 같이 들어 있는데
    사용자 쪽만 앞면이면 뒷면 각인을 비교할 짝이 없다. 각인이 뒷면에만 있는 약
    (앞은 제조사 로고, 뒤에 식별 문자)에서 재순위가 앞면만 보고 고르게 된다.
    앞뒤 두 장을 받아 판정에는 쓰면서 재순위에서만 버리던 것을 고친 것이다.

    뒷면은 가운데 확대본까지 보내지 않는다 — 사진이 늘수록 요청이 커지고 큰
    요청은 연결이 끊긴다(모듈 설명 참고). 확대는 앞면에만 준다.
    """
    out = [_shrink(images[0], width, quality),
           _shrink(_center(images[0], 0.5), width, quality)]
    if len(images) > 1:
        out.append(_shrink(images[1], width, quality))
    return out


def rerank(images: list[bytes], cands: list[Candidate], seen: PillVision,
           model: str | None = None) -> list[int] | None:
    """후보 번호(0부터)를 다시 매긴 순서. Claude 가 아니거나 실패하면 None — 원래 순서를 쓴다."""
    model = model or config.PILL_LLM_MODEL
    if not model.startswith("claude") or not cands:
        return None
    from src.pills.vision_claude import ask_claude

    head = _HEAD_TWO if len(images) > 1 else _HEAD_ONE
    prompt = _PROMPT.format(head=head, table=_table(cands))
    try:
        sheet = make_sheet(cands)
        user = _user_images(images, 640, 85)
        try:
            data = ask_claude(user + [sheet], model, prompt, _SCHEMA)
        except ConnectionError:
            # 큰 요청이 끊기는 문제(vision_llm 참고) — 사용자 사진을 줄여 한 번 더.
            user = _user_images(images, 480, 80)
            data = ask_claude(user + [sheet], model, prompt, _SCHEMA)
    except Exception as exc:
        logger.warning("재순위 실패: %s", type(exc).__name__)
        return None

    order, seen_ = [], set()
    for n in data.get("order") or []:
        if isinstance(n, int) and 1 <= n <= len(cands) and n not in seen_:
            seen_.add(n)
            order.append(n - 1)
    return order or None
