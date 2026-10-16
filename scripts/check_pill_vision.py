"""알약 모양·색 판별을 식약처 정답과 대조해 채점한다.

    python scripts/check_pill_vision.py              # 일반의약품 60장
    python scripts/check_pill_vision.py --n 200      # 더 많이
    python scripts/check_pill_vision.py --all        # 전문의약품까지 섞어서
    python scripts/check_pill_vision.py --miss       # 틀린 것만 자세히

`drug_pills` 에 식약처가 붙인 모양·색 라벨이 있으므로, 사진을 받아 분석한 결과와
바로 비교할 수 있다. 추측이 아니라 숫자로 확인하는 것이 목적이다.

사진은 매번 받지 않고 `data/pill_cache/` 에 저장해 다시 쓴다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import requests  # noqa: E402
from sqlalchemy import text  # noqa: E402

from src.pills.db import read_only  # noqa: E402
from src.pills.vision import analyze  # noqa: E402

CACHE = PROJECT_ROOT / "data" / "pill_cache"


def _fetch(item_seq: str, url: str) -> bytes | None:
    """사진을 받아 캐시에 둔다. 식약처 서버에 같은 걸 반복해서 요청하지 않는다."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{item_seq}.jpg"
    if path.exists():
        return path.read_bytes()
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
    except Exception:
        return None
    path.write_bytes(r.content)
    return r.content


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="알약 모양·색 판별 채점")
    parser.add_argument("--n", type=int, default=60, help="채점할 사진 수")
    parser.add_argument("--all", action="store_true", help="전문의약품도 포함")
    parser.add_argument("--miss", action="store_true", help="틀린 것만 자세히 출력")
    args = parser.parse_args(argv)

    where = "" if args.all else " AND etc_otc_name = '일반의약품'"
    with read_only("60s") as session:
        rows = session.execute(text(f"""
            SELECT item_seq, item_name, drug_shape, color_class1, item_image
              FROM drug_pills
             WHERE item_image IS NOT NULL
               AND drug_shape IS NOT NULL
               AND color_class1 IS NOT NULL
               -- 복합색("노랑, 투명")은 첫 색만 보므로 단색만으로 채점한다
               AND color_class1 NOT LIKE '%,%'
               {where}
             -- 매번 같은 표본이어야 고치기 전후를 비교할 수 있다
             ORDER BY md5(item_seq)
             LIMIT :n
        """), {"n": args.n}).mappings().all()

    shape_ok = color_ok = both_ok = total = 0
    shape_miss: dict[tuple[str, str], int] = {}
    color_miss: dict[tuple[str, str], int] = {}

    for r in rows:
        raw = _fetch(r["item_seq"], r["item_image"])
        if raw is None:
            continue
        try:
            got = analyze(raw)
        except Exception as exc:
            print(f"  분석 실패 {r['item_seq']}: {exc}")
            continue

        total += 1
        s_ok = got.shape == r["drug_shape"]
        c_ok = got.color == r["color_class1"]
        shape_ok += s_ok
        color_ok += c_ok
        both_ok += s_ok and c_ok

        if not s_ok:
            shape_miss[(r["drug_shape"], got.shape)] = shape_miss.get((r["drug_shape"], got.shape), 0) + 1
        if not c_ok:
            color_miss[(r["color_class1"], got.color)] = color_miss.get((r["color_class1"], got.color), 0) + 1

        if args.miss and not (s_ok and c_ok):
            mark_s = "OK" if s_ok else f"★ {r['drug_shape']}→{got.shape}"
            mark_c = "OK" if c_ok else f"★ {r['color_class1']}→{got.color}"
            print(f"  {r['item_name'][:26]:<28} 모양 {mark_s:<16} 색 {mark_c}")
            print(f"       {got.detail}")

    if not total:
        print("채점할 사진을 받지 못했습니다. 네트워크를 확인하세요.")
        return 1

    print()
    print(f"=== {total}장 채점 ===")
    print(f"  모양 정확도   {shape_ok:>4} / {total}  ({shape_ok / total * 100:5.1f}%)")
    print(f"  색   정확도   {color_ok:>4} / {total}  ({color_ok / total * 100:5.1f}%)")
    print(f"  둘 다 맞음    {both_ok:>4} / {total}  ({both_ok / total * 100:5.1f}%)")

    if shape_miss:
        print("\n  모양 오답 (정답→예측)")
        for (want, got_), n in sorted(shape_miss.items(), key=lambda x: -x[1])[:8]:
            print(f"    {want} → {got_}   {n}건")
    if color_miss:
        print("\n  색 오답 (정답→예측)")
        for (want, got_), n in sorted(color_miss.items(), key=lambda x: -x[1])[:8]:
            print(f"    {want} → {got_}   {n}건")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
