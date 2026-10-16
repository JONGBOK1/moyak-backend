"""각인 OCR 정확도를 식약처 정답과 대조해 채점한다.

    python scripts/check_pill_ocr.py --engine easyocr --n 40
    python scripts/check_pill_ocr.py --engine google  --n 40
    python scripts/check_pill_ocr.py --miss            # 틀린 것만 자세히

각인은 알약 인식의 승부처다. 실측:

    모양+색만      후보에 정답 9.2%  · 1위 정답  0.0%
    모양+색+각인   후보에 정답 91.6% · 1위 정답 78.2%

위 91.6% 는 "각인을 완벽히 읽었을 때" 의 **상한선**이다.
실제 OCR 이 거기 얼마나 근접하는지를 여기서 잰다.

채점 기준 두 가지
  완전일치  읽은 글자가 정답과 똑같다 (공백·대소문자 무시)
  포함      정답이 읽은 글자 안에 들어 있다 — 검색에는 이것만 돼도 쓸 수 있다
"""
from __future__ import annotations

import argparse
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

from sqlalchemy import text  # noqa: E402

from src.pills.db import read_only  # noqa: E402
from src.pills.ocr import read_many  # noqa: E402
from src.pills.vision import analyze  # noqa: E402

CACHE = PROJECT_ROOT / "data" / "pill_cache"


#: 각인 자리에 쓰이지만 글자가 아닌 표시. 정답에서도 빼야 비교가 맞는다.
_NOT_TEXT = ("마크", "분할선")


def _tokens(s: str | None) -> set[str]:
    """비교용 토큰 집합. 대소문자·구분기호·**순서**를 무시한다.

    각인은 알약 표면 여기저기에 흩어져 있어서 OCR 이 읽는 순서가 정답과 다를 수 있다.
    실제로 'H54' 를 '54 H' 로 읽은 경우가 있었는데, 글자를 통째로 이어 붙여 비교하면
    이게 오답으로 잡힌다. 토큰 집합으로 보면 같은 것으로 처리된다.
    """
    t = (s or "").upper()
    for junk in _NOT_TEXT:
        t = t.replace(junk.upper(), " ")
    parts = re.split(r"[^A-Z0-9가-힣]+", t)
    return {p for p in parts if p}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="각인 OCR 채점")
    parser.add_argument("--engine", default="easyocr", choices=["easyocr", "google", "auto"])
    parser.add_argument("--n", type=int, default=40)
    parser.add_argument("--miss", action="store_true", help="틀린 것만 자세히")
    args = parser.parse_args(argv)

    with read_only("60s") as session:
        rows = session.execute(text("""
            SELECT item_seq, item_name, print_front, print_back
              FROM drug_pills
             WHERE item_image IS NOT NULL
               AND print_front IS NOT NULL AND print_front <> ''
               -- '마크' 는 글자가 아니라 회사 로고라는 뜻이라 OCR 로 못 읽는다
               AND print_front NOT IN ('마크', '분할선')
               AND etc_otc_name = '일반의약품'
             ORDER BY md5(item_seq) LIMIT :n
        """), {"n": args.n}).mappings().all()

    exact = contain = read_any = total = 0
    started = time.time()

    for r in rows:
        path = CACHE / f"{r['item_seq']}.jpg"
        if not path.exists():
            continue
        try:
            seen = analyze(path.read_bytes())
        except Exception:
            continue
        if seen.crop is None:
            continue

        total += 1
        got = read_many(seen.crops or [seen.crop], engine=args.engine)
        want_f, want_b = _tokens(r["print_front"]), _tokens(r["print_back"])
        g = _tokens(got)

        if g:
            read_any += 1
        # 완전일치: 앞면 또는 뒷면 각인과 토큰이 똑같다
        # bool() 로 감싸지 않으면 want_b 가 빈 집합일 때 set() 이 그대로 반환돼
        # 뒤에서 int += set 으로 터진다.
        is_exact = bool(g) and (g == want_f or (bool(want_b) and g == want_b))
        # 포함: 정답 토큰이 전부 읽은 글자 안에 있다 — 검색에는 이것만 돼도 쓸 수 있다
        is_in = bool(g) and bool(want_f) and want_f <= g
        exact += is_exact
        contain += is_exact or is_in

        if args.miss and not (is_exact or is_in):
            print(f"  {r['item_name'][:24]:<26} 정답 '{r['print_front']}' → 읽음 '{got or '못읽음'}'")

    if not total:
        print("채점할 사진이 없습니다. check_pill_vision.py 로 캐시를 먼저 채우세요.")
        return 1

    took = time.time() - started
    print()
    print(f"=== {total}장 · 엔진 {args.engine} ===")
    print(f"  뭐라도 읽음     {read_any:>4} / {total}  ({read_any / total * 100:5.1f}%)")
    print(f"  완전일치        {exact:>4} / {total}  ({exact / total * 100:5.1f}%)")
    print(f"  정답 포함       {contain:>4} / {total}  ({contain / total * 100:5.1f}%)  ← 검색에 쓸 수 있는 비율")
    print(f"  장당 {took / total:.2f}초")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
