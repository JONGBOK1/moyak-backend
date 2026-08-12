"""STEP 1: 원본 JSON 정제 → data/processed/eyakeunyo_clean.csv 저장

- HTML 태그 제거
- item_seq 기준 중복 제거 (완전성 점수가 더 높은 레코드 유지)
- 텍스트 필드의 None을 빈 문자열로 정규화
"""

import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src import config

TEXT_FIELDS = [
    "efcyQesitm",
    "useMethodQesitm",
    "atpnWarnQesitm",
    "atpnQesitm",
    "intrcQesitm",
    "seQesitm",
    "depositMethodQesitm",
]

RENAME_MAP = {
    "itemSeq": "item_seq",
    "itemName": "item_name",
    "entpName": "company",
    "efcyQesitm": "efficacy",
    "useMethodQesitm": "usage",
    "atpnWarnQesitm": "warning",
    "atpnQesitm": "precaution",
    "intrcQesitm": "interaction",
    "seQesitm": "side_effect",
    "depositMethodQesitm": "storage",
    "openDe": "open_date",
    "updateDe": "update_date",
    "itemImage": "item_image",
    "bizrno": "biz_no",
}

TAG_PATTERN = re.compile(r"<[^>]+>")
WHITESPACE_PATTERN = re.compile(r"[ \t]+")


def strip_html(text) -> str:
    if not isinstance(text, str):
        return ""
    text = TAG_PATTERN.sub("", text)
    text = WHITESPACE_PATTERN.sub(" ", text)
    return text.strip()


def load_raw() -> pd.DataFrame:
    raw_path = config.DATA_RAW_DIR / "eyakeunyo_raw.json"
    payload = json.loads(raw_path.read_text(encoding="utf-8"))
    return pd.DataFrame(payload["items"])


def clean(df: pd.DataFrame) -> pd.DataFrame:
    for field in TEXT_FIELDS:
        df[field] = df[field].apply(strip_html)

    # 완전성 점수: 텍스트 필드 + itemImage 중 값이 채워진 개수
    score_cols = TEXT_FIELDS + ["itemImage"]
    df["_completeness"] = df[score_cols].apply(
        lambda row: sum(1 for v in row if isinstance(v, str) and v.strip()), axis=1
    )

    df = df.sort_values("_completeness", ascending=False)
    before = len(df)
    df = df.drop_duplicates(subset="itemSeq", keep="first")
    removed = before - len(df)
    print(f"중복 제거: {removed}건 ({before} -> {len(df)})")

    df = df.drop(columns=["_completeness"])
    df = df.rename(columns=RENAME_MAP)

    # ID/코드성 필드는 문자열로 고정 (선행 0 손실, int/str 타입 불일치 방지)
    for col in ["item_seq", "biz_no", "open_date"]:
        df[col] = df[col].astype(str)

    df = df.sort_values("item_seq").reset_index(drop=True)
    return df


def save_processed(df: pd.DataFrame) -> Path:
    config.DATA_PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    out_path = config.DATA_PROCESSED_DIR / "eyakeunyo_clean.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    return out_path


def main():
    df = load_raw()
    print(f"원본 로드: {len(df)}건")
    df = clean(df)
    out_path = save_processed(df)
    print(f"저장 완료: {out_path} (총 {len(df)}건)")


if __name__ == "__main__":
    main()