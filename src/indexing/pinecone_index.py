"""STEP 4: 임베딩 결과(embeddings.jsonl) -> Pinecone 인덱스에 upsert

data/processed/embeddings.jsonl 이 없으면(=STEP 3 미실행) 안내 후 종료한다.
"""

import json
import sys
from pathlib import Path

from pinecone import Pinecone, ServerlessSpec

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src import config

INDEX_NAME = "moyak-eyakeunyo"
DIMENSION = 1536
METRIC = "cosine"
CLOUD = "aws"
REGION = "us-east-1"  # Pinecone 무료 티어(Starter) 지원 리전
UPSERT_BATCH_SIZE = 100


def get_client() -> Pinecone:
    if not config.PINECONE_API_KEY:
        raise RuntimeError("PINECONE_API_KEY가 .env에 설정되어 있지 않습니다.")
    return Pinecone(api_key=config.PINECONE_API_KEY)


def ensure_index(pc: Pinecone):
    if pc.has_index(INDEX_NAME):
        print(f"인덱스 '{INDEX_NAME}' 이미 존재. 재사용합니다.")
    else:
        pc.create_index(
            name=INDEX_NAME,
            dimension=DIMENSION,
            metric=METRIC,
            spec=ServerlessSpec(cloud=CLOUD, region=REGION),
        )
        print(f"인덱스 '{INDEX_NAME}' 생성 완료 (dimension={DIMENSION}, metric={METRIC})")
    return pc.Index(INDEX_NAME)


def load_embeddings() -> list[dict]:
    path = config.DATA_PROCESSED_DIR / "embeddings.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"{path} 가 없습니다. STEP 3(embedding.py)을 먼저 실행하세요.")
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def upsert_all(index, records: list[dict]):
    total_batches = (len(records) + UPSERT_BATCH_SIZE - 1) // UPSERT_BATCH_SIZE
    for batch_no, batch in enumerate(batched(records, UPSERT_BATCH_SIZE), start=1):
        vectors = [
            {
                "id": r["id"],
                "values": r["embedding"],
                # 검색 결과에서 원문을 바로 꺼내 쓰도록 text도 metadata에 포함 (STEP 5에서 사용)
                "metadata": {**r["metadata"], "text": r["text"]},
            }
            for r in batch
        ]
        index.upsert(vectors=vectors)
        print(f"{batch_no}/{total_batches} 배치 upsert 완료")


def main():
    pc = get_client()
    index = ensure_index(pc)
    records = load_embeddings()
    print(f"임베딩 로드: {len(records)}개")
    upsert_all(index, records)
    print("인덱싱 완료")


if __name__ == "__main__":
    main()