"""사진 → 후보 목록 전 과정을 채점한다.

    python scripts/check_pill_search.py                  # 각인 없이 (모양+색만)
    python scripts/check_pill_search.py --print          # 각인까지 안다고 가정 (OCR 상한선)
    python scripts/check_pill_search.py --ocr google     # 실제 OCR — 이게 진짜 성능
    python scripts/check_pill_search.py --ocr google --miss   # 틀린 것만 원인별로

진짜 중요한 숫자는 "모양·색을 정확히 맞췄는가" 가 아니라
**정답 약이 후보 목록에 들어왔는가** 다. 모양을 틀려도 이웃 모양까지 훑으면
후보에는 남기 때문이다.

`--print` 는 각인을 DB 에서 그대로 가져다 쓴다. OCR 이 완벽할 때
어디까지 올라가는지 보는 **상한선**이다. 실제 OCR 은 이보다 낮다.

OCR 결과는 `data/ocr_cache.json` 에 저장해 다시 쓴다. Google Vision 은 사진 한 장에
앞뒤 조각 2건을 부르고 무료 한도가 월 1,000건이라, 캐시가 없으면 한 번 돌릴 때마다
월 한도의 1/4 이 날아간다. 점수 계산이나 검색 조건만 고칠 때는 OCR 을 다시 부를
이유가 없으므로 캐시가 그대로 쓰인다.

**`app/pills/ocr.py` 의 전처리나 언어 설정을 고쳤으면 `--fresh` 로 돌려야 한다.**
안 그러면 예전 결과를 그대로 읽어 고친 효과가 안 보인다.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
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
from src.pills import search  # noqa: E402
from src.pills.ocr import read_many  # noqa: E402
from src.pills.vision import analyze  # noqa: E402

CACHE = PROJECT_ROOT / "data" / "pill_cache"
#: OCR 결과 캐시. 키는 "품목코드|엔진" 이다.
OCR_CACHE = PROJECT_ROOT / "data" / "ocr_cache.json"


def _norm(t: str | None) -> str:
    """각인 비교용으로 다듬는다. search.py 의 `_clean_imprint` 와 같은 방식이어야 한다."""
    from src.pills.search import _clean_imprint

    return _clean_imprint(t)


def _diagnose(row, got, imprint: str | None) -> tuple[str, str]:
    """후보에 정답이 없는 이유를 하나로 집어낸다.

    **각인이 맞았는지부터 본다.** 각인이 맞으면 모양·색이 틀려도 대개 후보에 남으므로,
    각인이 맞았는데도 놓쳤다면 원인은 모양·색이지 각인이 아니다. 순서를 거꾸로 했다가
    "한글 각인 9건"이라는 엉뚱한 결론을 낸 적이 있다.
    """
    from src.pills.search import _tokens

    want = f"{row['print_front'] or ''} {row['print_back'] or ''}".strip()
    want_n = _norm(want)
    name = (row["item_name"] or "")[:24]
    detail = f"{name:<26} 각인 정답={want or '없음':<14} 읽음={imprint or '-'}"

    shape_ok = got.shape == row["drug_shape"]
    color_ok = got.color == (row["color_class1"] or "").split(",")[0].strip()

    if not want_n:
        # 알약에 로고(마크)나 분할선만 있고 글자가 없다. OCR 로는 못 고친다.
        return "DB 에 각인이 없는 약", detail
    if not imprint:
        return "각인을 못 읽음", detail

    # search.py 와 똑같이 토큰을 만들어, 그중 하나라도 DB 각인에 닿는지 본다.
    toks = _tokens(imprint)
    if not toks:
        return "읽었는데 쓸 토큰이 안 남음", detail

    if any(t in want_n or want_n in t for t in toks):
        if not shape_ok:
            return f"각인은 맞음 · 모양 틀림 ({row['drug_shape']}->{got.shape})", detail
        if not color_ok:
            return f"각인은 맞음 · 색 틀림 ({row['color_class1']}->{got.color})", detail
        return "각인은 맞았는데 순위 밖", detail

    if re.search(r"[가-힣]", want_n):
        return "한글 각인", detail
    return "각인 오독", detail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="알약 검색 전 과정 채점")
    parser.add_argument("--n", type=int, default=120)
    parser.add_argument("--print", dest="use_print", action="store_true",
                        help="각인을 DB 에서 가져와 씀 (OCR 완벽할 때의 상한선)")
    parser.add_argument("--ocr", choices=["easyocr", "google", "auto"],
                        help="각인을 실제 OCR 로 읽음 — 이게 진짜 성능이다")
    parser.add_argument("--miss", action="store_true",
                        help="후보에 정답이 없는 건을 원인별로 보여준다")
    parser.add_argument("--fresh", action="store_true",
                        help="OCR 캐시를 쓰지 않고 다시 읽는다 (ocr.py 를 고쳤으면 필수)")
    args = parser.parse_args(argv)

    cache: dict[str, str | None] = {}
    if OCR_CACHE.exists() and not args.fresh:
        try:
            cache = json.loads(OCR_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    cache_hits = [0]

    with read_only("60s") as session:
        rows = session.execute(text("""
            SELECT item_seq, item_name, drug_shape, color_class1, print_front, print_back
              FROM drug_pills
             WHERE item_image IS NOT NULL AND drug_shape IS NOT NULL
               AND color_class1 IS NOT NULL AND color_class1 NOT LIKE '%,%'
               AND etc_otc_name = '일반의약품'
             ORDER BY md5(item_seq) LIMIT :n
        """), {"n": args.n}).mappings().all()

        top1 = top5 = found = total = 0
        sizes = []
        misses: list[tuple[str, str]] = []
        for r in rows:
            path = CACHE / f"{r['item_seq']}.jpg"
            if not path.exists():
                continue
            try:
                got = analyze(path.read_bytes())
            except Exception:
                continue
            total += 1

            # 각인 출처 세 가지: DB(상한선) / 실제 OCR(진짜 성능) / 없음
            if args.use_print:
                imprint = r["print_front"]
            elif args.ocr and got.crop is not None:
                key = f"{r['item_seq']}|{args.ocr}"
                if key in cache:
                    imprint = cache[key]
                    cache_hits[0] += 1
                else:
                    imprint = read_many(got.crops or [got.crop], engine=args.ocr)
                    cache[key] = imprint
            else:
                imprint = None

            cands = search.find(
                session,
                shape=got.shape, color=got.color,
                print_text=imprint, aspect=got.aspect,
                limit=30,
            )
            sizes.append(len(cands))
            seqs = [c.item_seq for c in cands]
            if r["item_seq"] in seqs:
                found += 1
                rank = seqs.index(r["item_seq"])
                top1 += rank == 0
                top5 += rank < 5
            elif args.miss:
                misses.append(_diagnose(r, got, imprint))

    if not total:
        print("채점할 사진이 없습니다. check_pill_vision.py 를 먼저 돌려 캐시를 채우세요.")
        return 1

    avg = sum(sizes) / len(sizes) if sizes else 0
    mode = ("모양+색+각인(상한선)" if args.use_print
            else f"모양+색+각인({args.ocr} 실제)" if args.ocr
            else "모양+색만")
    print(f"\n=== {total}장 · {mode} ===")
    print(f"  후보 안에 정답 있음   {found:>4} / {total}  ({found / total * 100:5.1f}%)")
    print(f"  1위가 정답           {top1:>4} / {total}  ({top1 / total * 100:5.1f}%)")
    print(f"  상위 5위 안          {top5:>4} / {total}  ({top5 / total * 100:5.1f}%)")
    print(f"  평균 후보 수         {avg:.1f}개")

    if args.ocr and not args.fresh:
        print(f"  (OCR 캐시 사용 {cache_hits[0]}장 — Google 호출 {(total - cache_hits[0]) * 2}건)")

    if args.ocr:
        OCR_CACHE.parent.mkdir(parents=True, exist_ok=True)
        OCR_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")

    if args.miss and misses:
        counts: dict[str, int] = {}
        for reason, _ in misses:
            counts[reason] = counts.get(reason, 0) + 1
        print()
        print(f"=== 놓친 {len(misses)}건 · 원인별 ===")
        for reason, n in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"  {n:>3}건  {reason}")
        print()
        print("=== 놓친 건 상세 ===")
        for reason, detail in sorted(misses):
            print(f"  [{reason}] {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
