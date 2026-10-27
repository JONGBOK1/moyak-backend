"""키오스크에서 약사 승인 없이 바로 살 수 있는 상품을 채운다.

    python scripts/seed_kiosk_products.py            # 무엇을 넣을지 보여만 준다
    python scripts/seed_kiosk_products.py --apply    # 실제로 넣는다
    python scripts/seed_kiosk_products.py --clean    # 여기서 넣은 것만 지운다

**왜 `products` 와 `approved_purchases` 가 나뉘어 있나**
    자판기에서 의약품을 파는 건 약사법상 규제 영역이다. 그래서 흐름을 둘로 갈라 뒀다.

      products            의약외품·화장품·의료기기 → **약사 승인 없이 바로 결제**
      approved_purchases  일반/전문의약품        → 약사가 상담 후 승인해야 수령

    따라서 이 표에 **일반의약품을 넣으면 "약사 없이 약을 판다"가 되어** 프로젝트의
    규제 설명이 무너진다. 아래 목록은 그래서 전부 의약외품·화장품·의료기기다.

    헷갈리기 쉬운 것 (전부 일반의약품이라 여기 넣지 않았다):
      후시딘·마데카솔 연고, 아이봉 눈세정액, 인공눈물(점안액), 포비돈 소독약,
      타이레놀 같은 진통제
    이것들을 자판기에서 팔려면 `approved_purchases` 쪽(약사 승인)으로 가야 한다.

전부 가상 재고다(`is_sample = true`). 실제 발주·정산과는 무관하다.
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

from contextlib import contextmanager  # noqa: E402

from sqlalchemy import text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from src.pills.db import engine  # noqa: E402


@contextmanager
def session_scope():
    """쓰기용 세션. `src/pills/db.py` 의 read_only() 는 조회 전용이라 쓸 수 없다."""
    eng = engine()
    if eng is None:
        raise RuntimeError("DB 주소가 없습니다. .env 의 PILL_DATABASE_URL 을 확인하세요.")
    with Session(eng) as s:
        yield s
        s.commit()

#: 키오스크 화면의 카테고리 탭과 같은 값이어야 한다
#: (`src/api/static/kiosk/otc-list/index.html` 의 TAB_CATEGORIES).
CAT_WOUND = "상처/연고"
CAT_ETC = "안대/밴드/기타"

#: (품목코드, 품목명, 제조사, 카테고리, 뱃지, 가격, 기본재고, 구분)
#: 품목코드는 의약외품이라 식약처 낱알식별 코드가 없다. 겹치지 않게 임의로 붙였다.
#: (품목코드, 품목명, 제조사, 카테고리, 뱃지, 가격, 기본재고, 구분)
#:
#: **앞의 6종은 이름을 바꾸면 안 된다.** 키오스크 화면(`kiosk/otc-list/index.html`)이
#: Figma 카드 6개를 **상품명으로 찾아** productId 를 붙인다. 이름이 달라지면 장바구니에
#: 담기지 않는다. 품목코드도 기존 값을 그대로 쓴다.
#: 나머지는 새로 넣는 것이라 자유롭게 지어도 된다(코드 901xxxxxx 대역).
PRODUCTS = [
    # ── 키오스크 화면이 이름으로 찾는 6종 (수정 금지) ─────────────
    ("196500043", "대일밴드 뉴 표준형 20매", "대일화학", CAT_ETC, "상처 보호", 3500, 30, "의약외품"),
    ("202101234", "메디폼 H뷰티", "먼디파마", CAT_WOUND, "상처 보호", 4000, 20, "의료기기"),
    ("202209876", "KF94 보건용 마스크", "크린앤사이언스", CAT_ETC, "마스크", 3800, 50, "의약외품"),
    ("199900001", "리스테린 구강청결제", "존슨앤드존슨", CAT_WOUND, "구강 위생", 6000, 15, "의약외품"),
    ("200300002", "알콜스왑", "더가든오브내추럴", CAT_WOUND, "소독용품", 3000, 40, "의약외품"),
    ("200500003", "좋은느낌 생리대", "깨끗한나라", CAT_ETC, "여성용품", 4000, 25, "의약외품"),

    # ── 밴드 (요청: 두세 가지) ────────────────────────────────────
    ("901000002", "대일밴드 방수형 10매", "대일화학", CAT_ETC, "방수 밴드", 3000, 25, "의약외품"),
    ("901000003", "대일밴드 어린이 캐릭터 15매", "대일화학", CAT_ETC, "어린이용", 3200, 20, "의약외품"),
    ("901000004", "메디폼 하이드로콜로이드 밴드 10매", "먼디파마", CAT_WOUND, "습윤 드레싱", 6500, 18, "의료기기"),

    # ── 알콜솜 ────────────────────────────────────────────────────
    ("901000012", "소독용 에탄올 솜 200매", "제일퍼스트", CAT_WOUND, "소독용품", 4500, 30, "의약외품"),

    # ── 연고 (약 성분이 없는 것만 — 후시딘·마데카솔은 일반의약품) ──
    ("901000021", "바세린 오리지널 50g", "유니레버", CAT_WOUND, "보습", 5000, 22, "화장품"),
    ("901000022", "바세린 립테라피 7g", "유니레버", CAT_WOUND, "입술 보습", 4000, 28, "화장품"),

    # ── 안약 (점안액은 일반의약품이라 제외, 렌즈·안대만) ──────────
    ("901000031", "콘택트렌즈 다목적 용액 360ml", "바슈롬", CAT_ETC, "렌즈 관리", 9000, 12, "의약외품"),
    ("901000032", "일회용 렌즈 세정액 15ml x5", "바슈롬", CAT_ETC, "렌즈 관리", 5500, 16, "의약외품"),
    ("901000033", "의료용 안대 5매", "오공", CAT_ETC, "안대", 2500, 20, "의료기기"),

    # ── 생리대 ────────────────────────────────────────────────────
    ("901000042", "좋은느낌 대형 14매", "깨끗한나라", CAT_ETC, "여성용품", 4300, 22, "의약외품"),
    ("901000043", "좋은느낌 오버나이트 10매", "깨끗한나라", CAT_ETC, "여성용품", 4800, 18, "의약외품"),
    ("901000044", "순수한면 팬티라이너 40매", "엘지생활건강", CAT_ETC, "여성용품", 3500, 30, "의약외품"),

    # ── 마스크 ────────────────────────────────────────────────────
    ("901000052", "KF80 보건용 마스크 소형", "크린앤사이언스", CAT_ETC, "마스크", 3300, 40, "의약외품"),
    ("901000053", "일회용 덴탈 마스크 50매", "웰킵스", CAT_ETC, "마스크", 7000, 24, "의약외품"),

    # ── 구강 ──────────────────────────────────────────────────────
    ("901000062", "가그린 레귤러 100ml", "동아제약", CAT_WOUND, "구강 위생", 2500, 26, "의약외품"),
]

#: 어느 자판기에 넣을지는 **DB에서 읽는다.** 예전엔 ["VM-001", "VM-006", "VM-007"] 로
#: 박아 뒀는데, 그 뒤 자판기가 9대로 늘면서 나머지 6대에는 의약외품이 하나도 없어
#: 키오스크 상품 화면이 빈 채로 떴다. 자판기를 추가할 때마다 이 파일을 고치게 만들면
#: 또 어긋나므로, 운영 중인 자판기를 그때그때 가져온다.
def machine_codes(session) -> list[str]:
    """운영 중인 자판기 코드. 없으면 빈 목록(이 스크립트는 자판기를 만들지 않는다)."""
    return [r[0] for r in session.execute(text("""
        SELECT code FROM vending_machines
         WHERE is_active IS NOT FALSE
      ORDER BY code
    """))]


#: 자판기마다 재고를 다르게 둔다 — 어디나 똑같이 쌓여 있으면 화면이 가짜처럼 보인다.
#: 자판기 수가 몇 대든 되도록 비율로 돌린다(예전엔 3칸짜리 목록이라 4대째부터 터졌다).
#: 앞 세 자리는 예전 값(1.0 / base-7 / base//3)과 비슷하게 둬서, 이미 채워 둔
#: VM-001·006·007 의 재고가 크게 출렁이지 않게 한다.
#: **0 은 만들지 않는다.** 재고 0 이면 키오스크에 '품절' 로 뜨는데, 지금 고치려는 문제가
#: "상품이 안 보인다" 라서 시연 중에 또 빈 화면처럼 읽힐 수 있다. 품절 화면을 봐야 하면
#: 그 자판기 한 품목만 따로 0 으로 내리는 편이 분명하다.
_STOCK_RATIOS = (1.0, 0.72, 0.9, 0.35, 0.6, 0.78, 0.33, 0.85, 0.5)


def _machine_stock(base: int, idx: int) -> int:
    """idx 번째 자판기의 재고. 최소 1개는 남긴다(0 이면 품절로 보여 혼동된다)."""
    return max(1, round(base * _STOCK_RATIOS[idx % len(_STOCK_RATIOS)]))


def preview(machines: list[str] | None = None) -> None:
    n = len(machines) if machines else 0
    where = f"자판기 {n}대 ({', '.join(machines)})" if machines else "자판기 (DB에서 읽음)"
    print(f"상품 {len(PRODUCTS)}종 × {where} = {len(PRODUCTS) * n}행\n")
    kinds: dict[str, int] = {}
    for *_, kind in PRODUCTS:
        kinds[kind] = kinds.get(kind, 0) + 1
    print("  구분별: " + " · ".join(f"{k} {v}종" for k, v in sorted(kinds.items())))
    print()
    for seq, name, company, cat, badge, price, stock, kind in PRODUCTS:
        print(f"  {name:<32} {price:>6,}원  재고 {stock:>2}  [{kind}] {cat}")


def apply(session, machines: list[str]) -> None:
    """있으면 갱신, 없으면 추가.

    **지우고 새로 넣으면 안 된다.** 이미 주문(`kiosk_order_items`)에 걸린 상품이 있어서
    외래키에 막히고, 설령 지워지더라도 주문 내역이 깨진다. 같은 (품목코드, 자판기) 는
    갱신한다.
    """
    added = updated = 0
    for seq, name, company, cat, badge, price, stock, _kind in PRODUCTS:
        for i, machine in enumerate(machines):
            params = {"seq": seq, "name": name, "co": company, "cat": cat,
                      "badge": badge, "price": price,
                      "stock": _machine_stock(stock, i), "m": machine}
            hit = session.execute(text("""
                UPDATE products
                   SET item_name = :name, company = :co, category = :cat, badge = :badge,
                       price = :price, stock = :stock, is_sample = true
                 WHERE item_seq = :seq AND machine_id = :m
            """), params).rowcount
            if hit:
                updated += hit
                continue
            session.execute(text("""
                INSERT INTO products
                    (item_seq, item_name, company, category, badge, price, stock,
                     machine_id, is_sample)
                VALUES (:seq, :name, :co, :cat, :badge, :price, :stock, :m, true)
            """), params)
            added += 1
    print(f"새로 넣은 것 {added}행 · 갱신한 것 {updated}행")


def clean(session) -> None:
    seqs = [p[0] for p in PRODUCTS]
    # 주문에 걸린 상품은 지우지 않는다 — 외래키로 막히고, 지우면 주문 내역이 깨진다.
    used = session.execute(text("""
        SELECT DISTINCT p.item_seq FROM products p
          JOIN kiosk_order_items i ON i.product_id = p.id
         WHERE p.item_seq = ANY(:s)
    """), {"s": seqs}).scalars().all()
    target = [s for s in seqs if s not in used]
    n = session.execute(text("DELETE FROM products WHERE item_seq = ANY(:s) AND is_sample"),
                        {"s": target}).rowcount
    print(f"{n}행 지웠습니다." + (f" (주문에 걸린 {len(used)}종은 남겨 둠)" if used else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="키오스크 의약외품 채우기")
    ap.add_argument("--apply", action="store_true", help="실제로 넣는다")
    ap.add_argument("--clean", action="store_true", help="여기서 넣은 것만 지운다")
    args = ap.parse_args(argv)

    with session_scope() as session:
        if args.clean:
            clean(session)
            return 0

        machines = machine_codes(session)
        if not machines:
            print("운영 중인 자판기가 없습니다. vending_machines 를 먼저 확인하세요.")
            return 1

        if not args.apply:
            preview(machines)
            print("\n실제로 넣으려면 --apply 를 붙이세요.")
            return 0

        apply(session, machines)

        print("\n자판기별 상품 수")
        for r in session.execute(text("""
            SELECT machine_id, count(*) AS n, sum(stock) AS stock
              FROM products GROUP BY machine_id ORDER BY machine_id
        """)).mappings():
            print(f"  {r['machine_id']:<10} {r['n']:>3}종 · 재고 합계 {r['stock']:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
