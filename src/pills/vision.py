"""알약 사진에서 모양과 색을 뽑아낸다.

딥러닝을 쓰지 않는다. 알약은 모양이 단순하고(원형·타원형·장방형이 94%),
색도 식약처가 16가지로만 분류하기 때문에 **윤곽선과 색 통계**로 충분하다.

왜 이 방법으로 충분한가 — 실측:

    모양만        11그룹  · 평균 후보 2,304개
    모양+색      145그룹  · 평균 후보   175개
    모양+색+각인 19,025그룹 · 평균 후보  1.3개   ← 각인이 승부처

즉 모양·색은 "후보를 175개로 줄이는" 역할이고, 최종 확정은 각인 OCR 이 한다.
그래서 모양·색에 무거운 모델을 쓸 이유가 없다.

정답(식약처 라벨)이 `drug_pills` 에 들어 있으므로 `scripts/check_pill_vision.py` 로
언제든 채점할 수 있다.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

#: DB `drug_pills.drug_shape` 에 쓰이는 어휘. 여기 없는 값을 내보내면 조회가 실패한다.
SHAPES = ("원형", "타원형", "장방형", "삼각형", "사각형", "마름모형",
          "오각형", "육각형", "팔각형", "반원형", "기타")

#: DB `drug_pills.color_class1` 어휘와 대표 색상(HSV 기준값).
#: H 는 OpenCV 기준 0~179 (일반적인 0~360 의 절반).
#:
#: **추측이 아니라 식약처 사진 119장을 재서 정했다** (`scripts/check_pill_vision.py`):
#:
#:     색    n    H              S              V
#:     하양  31   —              3.9            233
#:     갈색  23   19.7           152            99     ← 주황과 H 가 같다
#:     주황  17   10.8           186            237    ← 구분은 밝기(V)
#:     초록  11   70.3           180            108
#:     노랑  10   34.4           147            226
#:     분홍   8   (0 과 179 로 갈림)  65.6      231    ← 채도가 낮은 게 특징
#:     연두   7   51.4           100            202
#:     파랑   5   99.2           145            174
#:     빨강   5   2.4            207            161
#:     검정   2   4.5            67             38     ← 채도가 높아도 검정이다
_COLOR_REFS: tuple[tuple[str, int, int, int], ...] = (
    # (이름,   H,   S,   V)
    ("빨강",    2, 207, 161),
    ("주황",   11, 186, 237),
    ("갈색",   20, 152,  99),
    ("노랑",   32, 147, 226),
    ("연두",   51, 100, 202),
    ("초록",   70, 180, 108),
    ("청록",   88, 170, 180),
    ("파랑",   99, 145, 174),
    ("남색",  115, 200, 110),
    ("보라",  140, 150, 170),
    ("자주",  165, 170, 140),
    ("분홍",  175,  66, 231),
)

@dataclass
class PillVision:
    """사진 한 장에서 뽑아낸 것."""

    shape: str
    color: str
    #: 0~1. 낮으면 사진이 흐리거나 알약을 못 찾은 것이다.
    confidence: float
    #: 알약만 잘라낸 이미지. 각인 OCR 에 넘긴다.
    crop: np.ndarray | None = field(default=None, repr=False)
    #: 사진에서 찾은 **모든** 알약 조각 (점수 높은 순, crop 이 첫 번째).
    #: 식약처 사진은 같은 약을 앞뒤로 두 개 찍어서 각인이 서로 다르다 —
    #: 실측상 25장 전부 2개 이상 잡혔다. 둘 다 읽으면 각인을 더 잡을 수 있다.
    crops: list = field(default_factory=list, repr=False)
    #: 알약의 가로세로비(긴변/짧은변). 사진 배율과 무관해서 폰 사진에서도 잴 수 있다.
    #: 실측상 이것만 더해도 후보가 평균 53.2개 → 11.4개로 줄어든다.
    aspect: float | None = None
    #: 판단 근거 — 왜 이 모양이라고 봤는지. 디버깅과 화면 표시에 쓴다.
    detail: dict = field(default_factory=dict)


def _find_pill(bgr: np.ndarray) -> list:
    """사진에서 알약 윤곽을 찾는다.

    배경색을 가정하지 않는다. 식약처 사진은 **파란 격자 배경**이고 사용자가 찍은
    사진은 책상이나 손바닥이라, "흰 배경" 을 전제하면 둘 다 틀린다.

    대신 **테두리 픽셀을 배경으로 본다.** 알약은 가운데 놓이고 사진 가장자리는
    거의 항상 배경이기 때문이다. 배경색과 충분히 다른 영역만 남긴다.

    식약처 사진에는 같은 알약이 앞뒤로 두 개 찍혀 있고, 위쪽 눈금자와
    오른쪽 아래 로고도 같이 잡힌다. 그래서 **가운데에 가깝고 큰 것** 을 고른다.
    """
    h, w = bgr.shape[:2]
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)

    # 테두리 20px 에서 배경색을 추정한다. 평균 대신 중앙값 — 격자선 같은 무늬에 안 흔들린다.
    b = 20
    border = np.concatenate([
        lab[:b].reshape(-1, 3), lab[-b:].reshape(-1, 3),
        lab[:, :b].reshape(-1, 3), lab[:, -b:].reshape(-1, 3),
    ])
    bg = np.median(border, axis=0)

    # 배경색과의 거리. Lab 공간이라 사람 눈이 느끼는 색 차이와 비슷하게 나온다.
    dist = np.linalg.norm(lab.astype(np.float32) - bg, axis=2)
    # 임계값을 고정하지 않는다. 사진마다 대비가 달라서, 분포를 보고 정한다.
    thr = max(18.0, float(np.percentile(dist, 90)) * 0.45)
    mask = (dist > thr).astype(np.uint8) * 255

    k = np.ones((9, 9), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k)

    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    frame = h * w
    cands = []
    for c in cnts:
        area = cv2.contourArea(c)
        ratio = area / frame
        # 너무 작으면 글자·먼지, 너무 크면 배경을 통째로 잡은 것이다.
        if not (0.004 <= ratio <= 0.60):
            continue
        x, y, cw, ch = cv2.boundingRect(c)
        cx, cy = x + cw / 2, y + ch / 2
        # 가운데로부터의 거리 (0=정중앙, 1=모서리)
        off = ((cx - w / 2) ** 2 + (cy - h / 2) ** 2) ** 0.5 / ((w ** 2 + h ** 2) ** 0.5 / 2)
        # **배경과 얼마나 다른가** 도 같이 본다. 크고 가운데에 있는 것만 고르면,
        # 책상 그림자처럼 임계값을 겨우 넘은 넓은 얼룩이 알약을 이긴다 — 실제
        # 사용자 사진에서 배경을 알약으로 잡아 노란 알약을 '회색' 이라고 답했다.
        # 진짜 알약은 배경에서 훨씬 멀리 떨어져 있다.
        m = np.zeros((h, w), np.uint8)
        cv2.drawContours(m, [c], -1, 255, -1)
        far = float(np.mean(dist[m > 0])) / thr      # 1.0 = 임계값 수준, 클수록 확실
        cands.append((ratio * (1.0 - 0.6 * off) * min(far, 3.0), c))

    if not cands:
        return []
    # 점수 높은 순. 셋을 넘으면 글자·먼지일 가능성이 커서 자른다.
    cands.sort(key=lambda t: -t[0])
    return [c for _, c in cands[:3]]


def _classify_shape(contour: np.ndarray) -> tuple[str, float, dict]:
    """윤곽선의 비율로 모양을 정한다.

    기준은 추측이 아니라 식약처 사진 120장을 재서 정했다
    (`scripts/check_pill_vision.py`, 일반의약품 표본):

        모양     n    원형도              가로세로비            채움율
        장방형  30   0.64 (0.18~0.83)   2.70 (1.49~14.98)   0.88 (0.72~1.00)
        원형    17   0.86 (0.40~0.90)   1.01 (1.00~1.14)    0.78 (0.69~0.80)
        타원형  12   0.74 (0.44~0.82)   1.98 (1.63~2.51)    0.83 (0.74~0.88)

    **가로세로비가 거의 다 가른다.** 원형은 1.14 를 안 넘고, 길쭉한 것은 1.49 부터다.
    겹치는 구간(타원형 1.63~2.51 vs 장방형 1.49~) 은 채움율과 원형도로 가른다 —
    타원은 매끄러워 원형도가 높고, 장방형은 모서리가 있어 사각형을 꽉 채운다.

    **꼭짓점 개수는 쓰지 않는다.** 같은 120장에서 재보니 원형 8.4, 타원형 7.9,
    장방형 7.9 로 전부 8 근처였다. 둥근 윤곽도 8각형으로 근사되기 때문이다.
    이걸 쓰다가 43건이 팔각형으로 잘못 분류됐다.

    다각형(삼각형·사각형·육각형...)은 전체의 3.7% 뿐이라 일부러 구분하지 않는다.
    94% 를 차지하는 원형·타원형·장방형을 정확히 맞추는 쪽이 이득이다.
    """
    area = cv2.contourArea(contour)
    peri = cv2.arcLength(contour, True)
    if peri == 0 or area == 0:
        return "기타", 0.0, {}

    circularity = 4 * np.pi * area / (peri * peri)
    (_, _), (w, h), _ = cv2.minAreaRect(contour)
    long_side, short_side = max(w, h), min(w, h)
    if short_side == 0:
        return "기타", 0.0, {}
    aspect = long_side / short_side
    rect_fill = area / (long_side * short_side)

    detail = {"circularity": round(float(circularity), 3),
              "aspect": round(float(aspect), 2),
              "rect_fill": round(float(rect_fill), 3)}

    # ── 둥글고 안 길쭉하다 → 원형 ────────────────────────────
    if aspect < 1.30:
        # 원형도가 낮으면 각진 알약(삼각형·사각형)일 수 있다. 확신을 낮춘다.
        conf = 0.9 if circularity > 0.80 else 0.5
        return "원형", conf, detail

    # ── 길쭉하다 → 타원형이냐 장방형이냐 ─────────────────────
    # 장방형은 모서리가 살아 있어 최소사각형을 꽉 채우고 둘레가 길어 원형도가 낮다.
    if rect_fill > 0.88 or circularity < 0.68:
        return "장방형", 0.75, detail
    return "타원형", 0.75, detail


def _classify_color(bgr: np.ndarray, mask: np.ndarray) -> tuple[str, float, dict]:
    """알약 영역의 대표 색을 식약처 16색 중 하나로 고른다.

    가장자리는 그림자와 배경이 섞이므로 **안쪽만** 쓴다.
    평균 대신 중앙값을 쓴다 — 각인 글자(어두운 홈)가 평균을 끌어내리기 때문이다.
    """
    inner = cv2.erode(mask, np.ones((15, 15), np.uint8), iterations=1)
    if cv2.countNonZero(inner) < 50:
        inner = mask

    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    pix = hsv[inner > 0]
    if pix.size == 0:
        return "하양", 0.0, {}

    h, s, v = (int(np.median(pix[:, i])) for i in range(3))
    detail = {"h": h, "s": s, "v": v}

    # ── 무채색 ───────────────────────────────────────────────
    # 검정(V 38, S 67) 과 어두운 갈색(V 15~180, S 152) 이 밝기로는 안 갈린다.
    # 실측상 **채도**가 갈라준다 — 갈색은 색기가 남아 있고 검정은 없다.
    if v < 60 and s < 110:
        return "검정", 0.85, detail
    if s < 40:
        return ("하양", 0.9, detail) if v > 185 else ("회색", 0.8, detail)

    # ── 유채색 — 기준색과의 거리로 고른다 ──────────────────────
    # 손으로 if 를 쌓아 가르는 걸 시도했다가 정확도가 75.6% → 68.9% 로
    # 오히려 떨어졌다. 측정한 기준색과의 거리로만 판단하는 쪽이 낫다.
    best, best_dist = "하양", 1e9
    for name, rh, rs, rv in _COLOR_REFS:
        # 색상환은 순환하므로 H 거리는 짧은 쪽으로 잰다 (분홍이 0 과 179 양쪽에 걸친다).
        dh = min(abs(h - rh), 180 - abs(h - rh))
        # 갈색과 주황은 H 가 거의 같고 밝기로만 갈린다(99 vs 237).
        # 그래서 밝기 가중치를 색상만큼 높게 둔다.
        dist = (dh * 3.0) ** 2 + (abs(s - rs) * 0.8) ** 2 + (abs(v - rv) * 1.0) ** 2
        if dist < best_dist:
            best, best_dist = name, dist

    conf = max(0.3, min(1.0, 1.0 - (best_dist ** 0.5) / 260))
    return best, conf, detail


def analyze(image_bytes: bytes) -> PillVision:
    """사진 한 장을 분석한다. 알약을 못 찾으면 confidence 가 0 에 가깝게 나온다."""
    arr = np.frombuffer(image_bytes, np.uint8)
    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("이미지를 읽을 수 없습니다. JPEG/PNG 인지 확인하세요.")

    # 너무 크면 느리기만 하다. 긴 변을 900px 로 맞춘다.
    long_side = max(bgr.shape[:2])
    if long_side > 900:
        scale = 900 / long_side
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    contours = _find_pill(bgr)
    if not contours:
        return PillVision(shape="기타", color="하양", confidence=0.0,
                          detail={"error": "알약을 찾지 못했습니다"})

    def _cut(c):
        """윤곽 하나를 잘라낸다. 바깥은 지워서 배경 글자를 안 읽게 한다."""
        m = np.zeros(bgr.shape[:2], np.uint8)
        cv2.drawContours(m, [c], -1, 255, -1)
        x, y, w, h = cv2.boundingRect(c)
        return m, cv2.bitwise_and(bgr, bgr, mask=m)[y:y + h, x:x + w]

    # 모양·색은 제일 그럴듯한 하나로만 판단한다. 여러 개를 섞으면 오히려 흐려진다.
    mask, crop = _cut(contours[0])
    shape, shape_conf, shape_detail = _classify_shape(contours[0])
    color, color_conf, color_detail = _classify_color(bgr, mask)

    return PillVision(
        shape=shape,
        color=color,
        confidence=round(float(min(shape_conf, color_conf)), 3),
        crop=crop,
        crops=[crop] + [_cut(c)[1] for c in contours[1:]],
        aspect=shape_detail.get("aspect"),
        detail={"shape": shape_detail, "color": color_detail,
                "found": len(contours)},
    )
