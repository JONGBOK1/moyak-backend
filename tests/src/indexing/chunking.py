"""STEP 2: 정제된 CSV -> 필드별 청크 생성 -> data/processed/chunks.jsonl 저장

필드 하나당 청크 하나. 값이 비어있는 필드는 청크를 만들지 않는다.
메타데이터: item_seq, item_name, company, field
"""

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src import config

# CSV 컬럼명 -> (필드 키, 한글 라벨)
FIELD_SPECS = [
    ("efficacy", "효능"),
    ("usage", "사용법"),
    ("warning", "경고"),
    ("precaution", "주의사항"),
    ("interaction", "상호작용"),
    ("side_effect", "부작용"),
    ("storage", "보관법"),
]

ID_DTYPE_COLS = {"item_seq": str, "biz_no": str, "open_date": str}


def load_clean() -> pd.DataFrame:
    csv_path = config.DATA_PROCESSED_DIR / "eyakeunyo_clean.csv"
    df = pd.read_csv(csv_path, dtype=ID_DTYPE_COLS)
    return df


def build_chunks(df: pd.DataFrame) -> list[dict]:
    chunks = []
    for row in df.itertuples(index=False):
        for field_key, field_label in FIELD_SPECS:
            value = getattr(row, field_key)
            if not isinstance(value, str) or not value.strip():
                continue
            chunk = {
                "id": f"{row.item_seq}_{field_key}",
                "text": f"{row.item_name}의 {field_label}: {value.strip()}",
                "metadata": {
                    "item_seq": row.item_seq,
                    "item_name": row.item_name,
                    "company": row.company,
                    "field": field_key,
                },
            }
            chunks.append(chunk)
    return chunks


def save_chunks(chunks: list[dict]) -> Path:
    config.DATA_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.DATA_PROCESSED_DIR / "chunks.jsonl"
    with out_path.open("w", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    return out_path


def main():
    df = load_clean()
    print(f"정제 데이터 로드: {len(df)}건")
    chunks = build_chunks(df)
    out_path = save_chunks(chunks)
    print(f"저장 완료: {out_path} (총 {len(chunks)}개 청크, 품목당 평균 {len(chunks) / len(df):.1f}개)")


if __name__ == "__main__":
    main()
