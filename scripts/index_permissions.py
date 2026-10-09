"""식약처 의약품 허가정보(Supabase drug_permissions)를 Pinecone에 임베딩해 의미 검색이 되게 한다 (1회성 스크립트).

- 대상: 허가 상태 '정상'이고 e약은요(drugs)에 없는 품목 (주로 전문의약품, 약 3만 건)
- 저장 위치: 기존 인덱스 `moyak-eyakeunyo`의 **별도 namespace `permissions`**
  → 기존 e약은요 검색(기본 namespace) 결과에는 전혀 섞이지 않는다.
- 허가정보엔 효능 설명문이 없어서 제품명·전문/일반·약효분류·주성분·성상·보관법을 한 문서로 묶는다.
  약효분류가 비어 있는 품목(약 27%)은 같은 주성분을 가진 다른 품목의 분류로 채운다.
- id = "perm-{item_seq}" 이라 다시 실행해도 중복 없이 덮어쓴다. Supabase는 읽기만 한다.

실행: python scripts/index_permissions.py [--dry-run] [--limit N]
비용: text-embedding-3-small 기준 전체 약 0.1달러 미만.
"""

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
from sqlalchemy import text

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src import config  # noqa: E402
from src.map_database import create_database_engine  # noqa: E402
from src.rag.drug_facts import parse_main_ingredients  # noqa: E402

NAMESPACE = "permissions"
INDEX_NAME = "moyak-eyakeunyo"
BATCH = 500


def clean_ingredient(name: str) -> str:
    return re.sub(r"\([^)]*\)", "", name).strip()


def load_rows(limit: int | None):
    url = (os.getenv("DRUG_DATABASE_URL") or os.getenv("MAP_DATABASE_URL") or "").strip()
    engine = create_database_engine(url)
    with engine.connect() as conn, conn.begin():
        conn.execute(text("SET TRANSACTION READ ONLY"))
        rows = conn.execute(text("""
            SELECT p.item_seq, p.item_name, p.entp_name, p.etc_otc_name, p.class_name, p.main_item_ingr,
                   p.chart, p.storage_method, p.atc_code
            FROM public.drug_permissions p
            WHERE p.cancel_name = '정상'
              AND NOT EXISTS (SELECT 1 FROM public.drugs d WHERE d.item_seq = p.item_seq)
            ORDER BY p.item_seq
        """)).mappings().all()
    return rows[:limit] if limit else rows


def impute_classes(rows) -> dict[str, str]:
    """주성분 조합 → 가장 흔한 약효분류. 분류가 빈 품목을 같은 주성분 품목의 분류로 채우는 데 쓴다."""
    votes: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        key = "|".join(sorted(clean_ingredient(i) for i in parse_main_ingredients(r["main_item_ingr"])))
        if key and r["class_name"]:
            votes[key][r["class_name"]] += 1
    return {k: c.most_common(1)[0][0] for k, c in votes.items()}


def build_doc(r, class_by_ingr: dict[str, str]) -> tuple[str, dict]:
    ingredients = [clean_ingredient(i) for i in parse_main_ingredients(r["main_item_ingr"])]
    key = "|".join(sorted(ingredients))
    drug_class = r["class_name"] or class_by_ingr.get(key)
    parts = [f"구분: {r['etc_otc_name']}" if r["etc_otc_name"] else None,
             f"약효분류: {drug_class}" if drug_class else None,
             f"주성분: {', '.join(ingredients[:6])}" if ingredients else None,
             f"제조사: {r['entp_name']}" if r["entp_name"] else None,
             f"성상: {r['chart']}" if r["chart"] else None,
             f"보관: {r['storage_method']}" if r["storage_method"] else None]
    body = f"{r['item_name']}\n" + " | ".join(p for p in parts if p)
    meta = {
        "item_seq": r["item_seq"], "item_name": r["item_name"], "field": "permission",
        "etc_otc": r["etc_otc_name"] or "", "drug_class": drug_class or "", "source": "drug_permissions",
    }
    return body, meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="임베딩/업로드 없이 문서만 만들어 건수·예시·예상 토큰 출력")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    rows = load_rows(args.limit)
    class_by_ingr = impute_classes(rows)
    docs = [build_doc(r, class_by_ingr) for r in rows]
    filled = sum(1 for r, (_, m) in zip(rows, docs) if not r["class_name"] and m["drug_class"])
    approx_tokens = sum(len(t) for t, _ in docs) // 2  # 한글 대략치
    print(f"대상 {len(docs):,}건 · 분류 보완 {filled:,}건 · 예상 토큰 ~{approx_tokens:,} (≈${approx_tokens / 1e6 * 0.02:.3f})")
    print("예시:\n" + docs[0][0] if docs else "(없음)")
    if args.dry_run:
        return

    from langchain_openai import OpenAIEmbeddings
    from langchain_pinecone import PineconeVectorStore

    store = PineconeVectorStore(
        index_name=INDEX_NAME,
        embedding=OpenAIEmbeddings(model="text-embedding-3-small", openai_api_key=config.OPENAI_API_KEY),
        pinecone_api_key=config.PINECONE_API_KEY,
        namespace=NAMESPACE,
    )
    for start in range(0, len(docs), BATCH):
        chunk = docs[start:start + BATCH]
        store.add_texts(
            texts=[t for t, _ in chunk],
            metadatas=[m for _, m in chunk],
            ids=[f"perm-{m['item_seq']}" for _, m in chunk],
            namespace=NAMESPACE,
        )
        print(f"  업로드 {min(start + BATCH, len(docs)):,}/{len(docs):,}", flush=True)
    print("완료")


if __name__ == "__main__":
    main()
