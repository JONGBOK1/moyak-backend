"""STEP 3: 청크 텍스트 -> OpenAI 임베딩 -> data/processed/embeddings.jsonl 저장

실행 비용이 발생하므로, 예상 토큰/비용을 먼저 출력하고 사용자 확인(y) 후에만
실제 OpenAI API를 호출한다.
"""

import json
import sys
import time
from pathlib import Path

from openai import APIError, OpenAI, RateLimitError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src import config

EMBEDDING_MODEL = "text-embedding-3-small"
BATCH_SIZE = 100
MAX_RETRIES = 5
# 2026-08 기준 text-embedding-3-small 공식 단가. 실제 청구 전 OpenAI pricing 페이지에서 재확인할 것.
PRICE_PER_1M_TOKENS_USD = 0.02


def load_chunks() -> list[dict]:
    chunks_path = config.DATA_PROCESSED_DIR / "chunks.jsonl"
    with chunks_path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f]


def estimate_tokens(chunks: list[dict]) -> int:
    # 러프 추정치: 한글은 평균적으로 글자 2자당 1토큰 내외 (tiktoken 미사용, 어림값)
    total_chars = sum(len(c["text"]) for c in chunks)
    return total_chars // 2


def batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def embed_batch(client: OpenAI, texts: list[str]):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return client.embeddings.create(model=EMBEDDING_MODEL, input=texts)
        except (RateLimitError, APIError) as e:
            if attempt == MAX_RETRIES:
                raise
            wait = 2**attempt
            print(f"  요청 실패 ({attempt}/{MAX_RETRIES}), {wait}초 후 재시도: {e}")
            time.sleep(wait)


def already_done_ids(out_path: Path) -> set[str]:
    if not out_path.exists():
        return set()
    with out_path.open(encoding="utf-8") as f:
        return {json.loads(line)["id"] for line in f if line.strip()}


def embed_all(chunks: list[dict], client: OpenAI, out_path: Path) -> int:
    done_ids = already_done_ids(out_path)
    pending = [c for c in chunks if c["id"] not in done_ids]
    if done_ids:
        print(f"이전 실행에서 이미 완료된 {len(done_ids)}개는 건너뜁니다.")

    total_batches = (len(pending) + BATCH_SIZE - 1) // BATCH_SIZE
    count = len(done_ids)
    with out_path.open("a", encoding="utf-8") as f:
        for batch_no, batch in enumerate(batched(pending, BATCH_SIZE), start=1):
            texts = [c["text"] for c in batch]
            response = embed_batch(client, texts)
            for chunk, item in zip(batch, response.data):
                record = {**chunk, "embedding": item.embedding}
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            count += len(batch)
            print(f"{batch_no}/{total_batches} 배치 임베딩 완료 (누적 {count}개)")
    return count


def main():
    chunks = load_chunks()
    print(f"청크 로드: {len(chunks)}개")

    est_tokens = estimate_tokens(chunks)
    est_cost_usd = est_tokens / 1_000_000 * PRICE_PER_1M_TOKENS_USD
    print(f"예상 토큰 수: 약 {est_tokens:,} (어림값)")
    print(f"예상 비용: 약 ${est_cost_usd:.4f} (모델: {EMBEDDING_MODEL})")

    answer = input("실제로 OpenAI API를 호출해서 임베딩을 진행할까요? (y/N): ").strip().lower()
    if answer != "y":
        print("사용자가 취소했습니다. API를 호출하지 않았습니다.")
        return

    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY가 .env에 설정되어 있지 않습니다.")

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    out_path = config.DATA_PROCESSED_DIR / "embeddings.jsonl"
    config.DATA_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    total = embed_all(chunks, client, out_path)
    print(f"저장 완료: {out_path} (총 {total}개)")


if __name__ == "__main__":
    main()