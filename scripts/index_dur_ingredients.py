"""허가정보 주성분에서 DUR 성분을 찾아 `drug_ingredients` 를 보강한다.

**왜 필요한가** — 병용금기는 성분끼리 대조해야 판정이 된다. 그런데 색인(`drug_ingredients`,
식약처 DUR API 에서 받은 것)에 든 약이 8,216개(19%)뿐이라, 나머지 약을 먹는 환자에게는
약사 화면이 "확인하지 못했습니다"만 떴다. 실제로는 허가정보 `main_item_ingr` 가 99.9%
차 있고 거기에 성분이 적혀 있다 — 다만 이름이 염 형태로 달라서("페노바르비탈" vs
"페노바르비탈나트륨") 그대로는 대조가 안 됐다.

그래서 `drug_facts.normalize_ingredient`(염·수화물을 떼는 기존 함수)로 양쪽을 맞춰
다시 대조한다. 실측: 3,327개 약이 새로 걸려 19.1% → 26.9%.

**넣는 행은 `source='permission'` 으로 표시한다.** DUR API 가 준 원본(`source='dur'`)과
섞이지 않아야 나중에 되돌리거나 원본만 다시 받을 수 있다.

    python scripts/index_dur_ingredients.py            # 재보기만 (쓰지 않음)
    python scripts/index_dur_ingredients.py --write    # 실제로 넣기
    python scripts/index_dur_ingredients.py --revert   # 넣은 것만 도로 빼기

**오매칭을 막는 규칙**: 정규화한 이름이 2글자 이하면 쓰지 않는다. '염화칼륨' 이 '염화' 로
줄어드는데, 그러면 염화나트륨·염화벤잘코늄까지 같은 성분으로 잡힌다. 그런 성분은 염을
떼지 않은 원래 이름이 그대로 있을 때만 인정한다.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from src.consult.db import SessionLocal  # noqa: E402
from src.rag.drug_facts import normalize_ingredient, parse_main_ingredients  # noqa: E402

MIN_NORM_LEN = 3  # 이보다 짧게 줄어든 이름은 정규화 대조에 쓰지 않는다
SOURCE = "permission"


def build(session) -> tuple[list[tuple[str, str, str, str]], int]:
    """넣어야 할 (item_seq, ingr_code, ingr_name, item_name) 목록과, 이미 있던 약 수."""
    dur = session.execute(text("""
        SELECT DISTINCT ingr_code, ingr_name FROM drug_ingredients
         WHERE ingr_code IS NOT NULL AND ingr_name IS NOT NULL""")).all()

    by_norm: dict[str, tuple[str, str]] = {}   # 정규화 이름 → (코드, 대표 이름)
    by_exact: dict[str, tuple[str, str]] = {}  # 원래 이름(공백 제거) → (코드, 이름)
    for code, name in dur:
        by_exact.setdefault(name.replace(" ", ""), (code, name))
        n = normalize_ingredient(name)
        if len(n) >= MIN_NORM_LEN:
            by_norm.setdefault(n, (code, name))

    have = {r[0] for r in session.execute(text("SELECT DISTINCT item_seq FROM drug_ingredients"))}

    rows = session.execute(text("""
        SELECT item_seq, item_name, main_item_ingr FROM drug_permissions
         WHERE main_item_ingr IS NOT NULL AND main_item_ingr <> ''""")).all()

    out: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for seq, item_name, ingr in rows:
        if seq in have:
            continue
        for raw in parse_main_ingredients(ingr):
            hit = by_exact.get(raw.replace(" ", ""))
            if hit is None:
                n = normalize_ingredient(raw)
                hit = by_norm.get(n) if len(n) >= MIN_NORM_LEN else None
            if hit and (seq, hit[0]) not in seen:
                seen.add((seq, hit[0]))
                out.append((seq, hit[0], hit[1], item_name))
    return out, len(have)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="실제로 넣는다")
    ap.add_argument("--revert", action="store_true", help=f"source='{SOURCE}' 행만 지운다")
    args = ap.parse_args()

    with SessionLocal() as session:
        if args.revert:
            n = session.execute(text("DELETE FROM drug_ingredients WHERE source = :s"),
                                {"s": SOURCE}).rowcount
            session.commit()
            print(f"source='{SOURCE}' {n}행 삭제")
            return

        pairs, before = build(session)
        items = {p[0] for p in pairs}
        print(f"지금 색인된 약 {before:,}개")
        print(f"새로 걸리는 약 {len(items):,}개 · (약,성분) 쌍 {len(pairs):,}건")
        print(f"→ {before:,} → {before + len(items):,}개")

        if not args.write:
            print("\n예시:")
            for seq, code, name, item in pairs[:8]:
                print(f"   {item[:36]:<38} {name} ({code})")
            print("\n(재보기만 했습니다. 실제로 넣으려면 --write)")
            return

        session.execute(text("""
            INSERT INTO drug_ingredients (item_seq, ingr_code, ingr_name, item_name, source)
            VALUES (:seq, :code, :name, :item, :src)"""),
            [{"seq": s, "code": c, "name": n, "item": i, "src": SOURCE} for s, c, n, i in pairs])
        session.commit()
        after = session.execute(text("SELECT count(DISTINCT item_seq) FROM drug_ingredients")).scalar()
        print(f"\n넣었습니다. 색인된 약 {after:,}개")


if __name__ == "__main__":
    main()
