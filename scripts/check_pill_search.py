"""사진 → 후보 목록 전 과정을 채점한다.

    python scripts/check_pill_search.py                  # 각인 없이 (모양+색만)
    python scripts/check_pill_search.py --print          # 각인까지 안다고 가정 (OCR 상한선)
    python scripts/check_pill_search.py --ocr google     # 실제 OCR — 이게 진짜 성능
    python scripts/check_pill_search.py --ocr google --miss   # 틀린 것만 원인별로
    python scripts/check_pill_search.py --ocr llm        # 비전 LLM 이 모양·색·각인을 한 번에

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

`--ocr llm` 은 모양·색·각인을 전부 LLM 이 낸다(`app/pills/vision_llm.py`). 캐시 키에
모델 이름이 들어가므로 `--model` 을 바꾸면 그 모델로 새로 부른다.
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

from src import config  # noqa: E402
from src.pills.db import read_only  # noqa: E402
from src.pills import search  # noqa: E402
from src.pills.ocr import read_many  # noqa: E402
from src.pills.vision import analyze, analyze_and_read  # noqa: E402
from src.pills.vision_llm import LLMError, ask_llm, to_vision  # noqa: E402

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
    if "has_logo" in got.detail:
        detail += f"  로고={'Y' if got.detail['has_logo'] else 'N'}"

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


def _download_missing(rows) -> None:
    """캐시에 없는 식약처 사진을 받는다. 같은 걸 반복해서 요청하지 않는다."""
    from concurrent.futures import ThreadPoolExecutor

    from check_pill_vision import _fetch

    todo = [r for r in rows if r["item_image"] and not (CACHE / f"{r['item_seq']}.jpg").exists()]
    if not todo:
        return
    print(f"  사진 받는 중 {len(todo)}장 ...", flush=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        got = sum(b is not None for b in pool.map(lambda r: _fetch(r["item_seq"], r["item_image"]), todo))
    if got < len(todo):
        print(f"  못 받은 사진 {len(todo) - got}장 — 채점에서 빠진다")


def _prefetch_llm(rows, cache: dict, model: str, use_cache: bool, workers: int) -> None:
    """캐시에 없는 사진만 LLM 에 보낸다. 한 장에 수 초가 걸려 동시에 부른다.

    실패한 사진은 캐시에 넣지 않는다 — 다음에 돌릴 때 다시 시도된다.
    """
    from concurrent.futures import ThreadPoolExecutor

    todo = []
    for r in rows:
        key = f"{r['item_seq']}|llm|{model}"
        path = CACHE / f"{r['item_seq']}.jpg"
        if path.exists() and not (use_cache and key in cache):
            todo.append((key, path))
    if not todo:
        return
    print(f"  {model} 호출 {len(todo)}장 ...", flush=True)

    def call(item):
        key, path = item
        try:
            return key, ask_llm(path.read_bytes(), model=model)
        except (LLMError, ValueError):
            return key, None

    failed = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for key, data in pool.map(call, todo):
            if data is None:
                failed += 1
            else:
                cache[key] = data
    if failed:
        print(f"  호출 실패 {failed}장 — 이 사진들은 OpenCV 모양·색, 각인 없음으로 채점된다")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="알약 검색 전 과정 채점")
    parser.add_argument("--n", type=int, default=120)
    parser.add_argument("--offset", type=int, default=0,
                        help="앞에서 이만큼 건너뛴다. --offset 120 이면 기존 119장과 겹치지 않는 새 사진")
    parser.add_argument("--print", dest="use_print", action="store_true",
                        help="각인을 DB 에서 가져와 씀 (OCR 완벽할 때의 상한선)")
    parser.add_argument("--ocr", choices=["easyocr", "google", "auto", "llm"],
                        help="각인을 실제 OCR 로 읽음 — 이게 진짜 성능이다. "
                             "llm 은 모양·색까지 비전 LLM 이 낸다")
    parser.add_argument("--model", help="--ocr llm 에 쓸 모델 (기본: 설정의 PILL_LLM_MODEL)")
    parser.add_argument("--aspect", choices=["cv", "llm"], default="cv",
                        help="--ocr llm 일 때 가로세로비 출처. cv=OpenCV 로 잰 값, llm=LLM 눈대중")
    parser.add_argument("--workers", type=int, default=6, help="--ocr llm 동시 호출 수")
    parser.add_argument("--miss", action="store_true",
                        help="후보에 정답이 없는 건을 원인별로 보여준다")
    parser.add_argument("--retry", action="store_true",
                        help="실패하면 가운데를 잘라 다시 한다 (API 가 실제로 하는 것)")
    parser.add_argument("--fresh", action="store_true",
                        help="OCR 캐시를 쓰지 않고 다시 읽는다 (ocr.py 를 고쳤으면 필수)")
    args = parser.parse_args(argv)

    # **--fresh 여도 캐시 파일은 읽는다.** 읽지 않으면 마지막에 이번 결과만으로 파일을
    # 덮어써서 다른 엔진(google 등)의 캐시가 통째로 사라진다 — 실제로 `google` 키가
    # 사라지고 `google|retry` 만 남아 있었다. --fresh 는 "이번 키를 무시한다" 는 뜻이다.
    cache: dict = {}
    if OCR_CACHE.exists():
        try:
            cache = json.loads(OCR_CACHE.read_text(encoding="utf-8"))
        except Exception:
            cache = {}
    use_cache = not args.fresh
    cache_hits = [0]
    model = args.model or config.PILL_LLM_MODEL

    with read_only("60s") as session:
        rows = session.execute(text("""
            SELECT item_seq, item_name, drug_shape, color_class1, print_front, print_back,
                   item_image
              FROM drug_pills
             WHERE item_image IS NOT NULL AND drug_shape IS NOT NULL
               AND color_class1 IS NOT NULL AND color_class1 NOT LIKE '%,%'
               AND etc_otc_name = '일반의약품'
             ORDER BY md5(item_seq) LIMIT :n OFFSET :offset
        """), {"n": args.n, "offset": args.offset}).mappings().all()

        # **검색 가중치는 앞의 119장(--offset 0)을 보며 골랐다.** 그 숫자는 그 사진들에 맞춰져
        # 있을 수 있으므로, 정직한 성능은 --offset 120 처럼 겹치지 않는 사진으로 잰다.
        # 없는 사진은 식약처 서버에서 받아 둔다(한 번 받으면 data/pill_cache 에 남는다).
        _download_missing(rows)

        if args.ocr == "llm":
            _prefetch_llm(rows, cache, model, use_cache, args.workers)

        top1 = top5 = found = total = 0
        shape_ok = color_ok = 0
        tokens_in = tokens_out = llm_n = 0
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
            elif args.ocr == "llm":
                data = cache.get(f"{r['item_seq']}|llm|{model}")
                if data:
                    # 가로세로비만 OpenCV 값을 쓸 수 있다(LLM 눈대중과 비교용).
                    cv_aspect = got.aspect if args.aspect == "cv" else None
                    got, imprint = to_vision(data, aspect=cv_aspect)
                    u = data.get("usage") or {}
                    tokens_in += u.get("input", 0)
                    tokens_out += u.get("output", 0)
                    llm_n += 1
                else:
                    imprint = None          # 호출 실패 — 기존 모양·색으로 채점된다
            elif args.ocr and args.retry:
                key = f"{r['item_seq']}|{args.ocr}|retry"
                if use_cache and key in cache:
                    imprint = cache[key]
                    cache_hits[0] += 1
                else:
                    got, imprint = analyze_and_read(path.read_bytes(), engine=args.ocr)
                    cache[key] = imprint
            elif args.ocr and got.crop is not None:
                key = f"{r['item_seq']}|{args.ocr}"
                if use_cache and key in cache:
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
                has_logo=got.detail.get("has_logo"),   # LLM 만 준다. OCR 쪽은 None
                limit=30,
            )
            sizes.append(len(cands))
            shape_ok += got.shape == r["drug_shape"]
            color_ok += got.color == r["color_class1"]
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
    print(f"  모양 맞음            {shape_ok:>4} / {total}  ({shape_ok / total * 100:5.1f}%)")
    print(f"  색 맞음              {color_ok:>4} / {total}  ({color_ok / total * 100:5.1f}%)")

    if args.ocr == "llm":
        print(f"  ({model} · 응답 {llm_n}/{total}장 · 장당 입력 {tokens_in / max(llm_n, 1):.0f}"
              f" / 출력 {tokens_out / max(llm_n, 1):.0f} 토큰)")

    if args.ocr and args.ocr != "llm" and not args.fresh:
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
