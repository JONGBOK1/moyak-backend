"""약사 콘솔 약품 검색(/drugs/search)용 경량 인덱스를 만든다.

data/processed/는 .gitignore라 배포 서버(Render)에 없으므로, 검색에 필요한
3개 컬럼(item_seq, item_name, company)만 뽑아 src/api/drug_index.csv로 저장해
git에 포함시킨다. 정제 데이터가 갱신되면 이 스크립트를 다시 실행할 것.
"""

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.config import DATA_PROCESSED_DIR  # noqa: E402

SRC = DATA_PROCESSED_DIR / "eyakeunyo_clean.csv"
DST = Path(__file__).resolve().parent.parent / "src" / "api" / "drug_index.csv"


def _etc_otc(item_seqs: list[str]) -> dict[str, str]:
    """품목코드 → 전문의약품 / 일반의약품. 허가정보(Supabase)에서 읽는다.

    검색에서 **전문의약품을 빼려면** 이 구분이 필요하다(src/api/routes/drugs.py).
    정제 CSV(e약은요)에는 이 칸이 없어 DB 를 따로 본다. DB 를 못 읽으면 색인을
    만들지 않는다 — 칸이 빈 색인을 올리면 전문의약품이 검색에 되살아난다.
    """
    from src.rag import drug_facts
    engine = drug_facts.get_engine()
    if engine is None:
        raise SystemExit("허가정보 DB에 연결할 수 없습니다 (DRUG_DATABASE_URL 등 확인). "
                         "전문의약품을 거를 수 없어 색인을 만들지 않습니다.")
    from sqlalchemy import text
    with engine.connect() as conn:
        return {r[0]: r[1] for r in conn.execute(
            text("SELECT item_seq, etc_otc_name FROM public.drug_permissions "
                 "WHERE item_seq = ANY(:s)"), {"s": item_seqs})}


def main() -> None:
    with open(SRC, encoding="utf-8-sig", newline="") as f_in:
        rows = list(csv.DictReader(f_in))
    kind = _etc_otc([r["item_seq"] for r in rows])

    with open(DST, "w", encoding="utf-8", newline="") as f_out:
        writer = csv.writer(f_out)
        writer.writerow(["item_seq", "item_name", "company", "etc_otc"])
        for row in rows:
            writer.writerow([row["item_seq"], row["item_name"], row["company"],
                             kind.get(row["item_seq"], "")])
    etc = sum(1 for r in rows if kind.get(r["item_seq"]) == "전문의약품")
    print(f"{len(rows)}건 -> {DST} (전문의약품 {etc}건은 검색에서 제외됨)")


if __name__ == "__main__":
    main()
