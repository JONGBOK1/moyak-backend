"""각인 OCR 전처리 방식을 여러 개 만들어 같은 사진으로 비교한다.

    python scripts/tune_pill_ocr.py --n 25            # 전부 비교
    python scripts/tune_pill_ocr.py --n 25 --only A C  # 일부만

왜 필요한가
    각인은 흰 알약에 흰 음각이라 **대비가 거의 없다.** 사진을 열어 보면
    사람 눈에도 겨우 보이는 수준이다. 전처리를 어떻게 하느냐가 OCR 성능을 좌우한다.

    손으로 고치고 "좋아진 것 같다" 로 판단하면 틀린다 — 실제로 색 판정에서
    두 번 연속 악화시킨 적이 있다. 그래서 전부 숫자로 비교한다.

알약 영역(crop)은 한 번만 계산해 캐시에 저장하고, 전처리만 바꿔 가며 잰다.
"""
from __future__ import annotations

import argparse
import pickle
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import cv2  # noqa: E402
import numpy as np  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.pills.db import read_only  # noqa: E402
from src.pills.vision import analyze  # noqa: E402

CACHE = PROJECT_ROOT / "data" / "pill_cache"
CROPS = PROJECT_ROOT / "data" / "pill_crops.pkl"

_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        _reader = easyocr.Reader(["en"], gpu=False, verbose=False)
    return _reader


def _upscale(gray: np.ndarray, target: int) -> np.ndarray:
    short = min(gray.shape[:2])
    if short and short < target:
        f = target / short
        return cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_CUBIC)
    return gray


# ── 전처리 후보들 ────────────────────────────────────────────────
# 각각 알약 조각(BGR)을 받아 OCR 에 넣을 흑백 이미지를 돌려준다.

def prep_A(crop):
    """지금 쓰는 것 — 흑백 → 400px 확대 → CLAHE."""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = _upscale(g, 400)
    return cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(g)


def prep_B(crop):
    """더 크게 확대 — 작은 글자는 키우면 잘 읽힌다."""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = _upscale(g, 700)
    return cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8)).apply(g)


def prep_C(crop):
    """언샤프 마스킹 — 흐릿한 원본을 빼서 **경계만 남긴다**.

    각인은 색이 아니라 '파인 자국의 그림자'로 보인다. 전체 밝기를 올리는 것보다
    경계를 세우는 쪽이 각인에 맞는다.
    """
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = _upscale(g, 600)
    blur = cv2.GaussianBlur(g, (0, 0), 3)
    sharp = cv2.addWeighted(g, 1.8, blur, -0.8, 0)
    return cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(sharp)


def prep_D(crop):
    """모폴로지 블랙햇 — 밝은 바탕의 **어두운 가는 선**만 뽑는다.

    음각은 빛이 안 닿아 주변보다 어둡다. 블랙햇은 정확히 그런 것을 남긴다.
    """
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = _upscale(g, 600)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    bh = cv2.morphologyEx(g, cv2.MORPH_BLACKHAT, k)
    bh = cv2.normalize(bh, None, 0, 255, cv2.NORM_MINMAX)
    # OCR 은 '밝은 바탕에 어두운 글자'를 기대하므로 뒤집는다
    return cv2.bitwise_not(bh)


def prep_E(crop):
    """적응형 이진화 — 지역마다 기준을 달리해 흑백으로 가른다."""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = _upscale(g, 600)
    g = cv2.GaussianBlur(g, (3, 3), 0)
    return cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY, 31, 8)


def prep_F(crop):
    """C(언샤프) + D(블랙햇) 를 섞는다."""
    g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    g = _upscale(g, 600)
    blur = cv2.GaussianBlur(g, (0, 0), 3)
    sharp = cv2.addWeighted(g, 1.8, blur, -0.8, 0)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
    bh = cv2.morphologyEx(sharp, cv2.MORPH_BLACKHAT, k)
    bh = cv2.normalize(bh, None, 0, 255, cv2.NORM_MINMAX)
    return cv2.bitwise_not(bh)


VARIANTS = {"A": prep_A, "B": prep_B, "C": prep_C,
            "D": prep_D, "E": prep_E, "F": prep_F}

_NOT_TEXT = ("마크", "분할선")
_ALLOW = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/-+."


def _tokens(s: str | None) -> set[str]:
    t = (s or "").upper()
    for junk in _NOT_TEXT:
        t = t.replace(junk.upper(), " ")
    return {p for p in re.split(r"[^A-Z0-9가-힣]+", t) if p}


def _read(gray: np.ndarray) -> set[str]:
    try:
        parts = _get_reader().readtext(gray, detail=0, paragraph=False, allowlist=_ALLOW)
    except Exception:
        return set()
    return _tokens(" ".join(parts))


def build_crops(limit: int) -> list[dict]:
    """알약 영역을 한 번만 계산해 저장한다. 전처리 비교마다 다시 할 이유가 없다."""
    if CROPS.exists():
        data = pickle.loads(CROPS.read_bytes())
        if len(data) >= limit:
            return data[:limit]

    with read_only("60s") as s:
        rows = s.execute(text("""
            SELECT item_seq, item_name, print_front, print_back
              FROM drug_pills
             WHERE etc_otc_name = '일반의약품'
               AND print_front IS NOT NULL AND print_front <> ''
               AND print_front NOT IN ('마크', '분할선')
             ORDER BY md5(item_seq) LIMIT :n
        """), {"n": max(limit, 80)}).mappings().all()

    out = []
    for r in rows:
        p = CACHE / f"{r['item_seq']}.jpg"
        if not p.exists():
            continue
        try:
            v = analyze(p.read_bytes())
        except Exception:
            continue
        if v.crop is None or v.crop.size == 0:
            continue
        out.append({"name": r["item_name"], "crop": v.crop,
                    "front": r["print_front"], "back": r["print_back"]})
    CROPS.write_bytes(pickle.dumps(out))
    return out[:limit]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="각인 전처리 비교")
    parser.add_argument("--n", type=int, default=25)
    parser.add_argument("--only", nargs="*", help="비교할 방식 (A B C ...)")
    args = parser.parse_args(argv)

    items = build_crops(args.n)
    if not items:
        print("알약 조각을 만들지 못했습니다. check_pill_vision.py 로 사진을 먼저 받으세요.")
        return 1
    print(f"대상 {len(items)}장\n")

    names = args.only or list(VARIANTS)
    results = []
    for key in names:
        fn = VARIANTS.get(key)
        if fn is None:
            continue
        hit = read_any = 0
        t0 = time.time()
        for it in items:
            try:
                g = _read(fn(it["crop"]))
            except Exception:
                g = set()
            if g:
                read_any += 1
            wf, wb = _tokens(it["front"]), _tokens(it["back"])
            if g and (g == wf or (wb and g == wb) or (wf and wf <= g)):
                hit += 1
        took = time.time() - t0
        results.append((key, hit, read_any, took))
        print(f"  {key}  {fn.__doc__.splitlines()[0][:38]:<40} "
              f"맞음 {hit:>3}/{len(items)} ({hit / len(items) * 100:5.1f}%)  "
              f"읽음 {read_any:>3}  {took / len(items):.1f}초/장")

    print()
    best = max(results, key=lambda r: r[1])
    print(f"  최고: {best[0]}  {best[1]}/{len(items)} ({best[1] / len(items) * 100:.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
