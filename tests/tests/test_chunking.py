import pandas as pd

from src.indexing.chunking import build_chunks

FIELDS = ["efficacy", "usage", "warning", "precaution", "interaction", "side_effect", "storage"]


def clean_row(**overrides) -> dict:
    row = {"item_seq": "195700020", "item_name": "활명수", "company": "동화약품(주)"}
    row.update({field: None for field in FIELDS})
    row.update(overrides)
    return row


def make_clean_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_build_chunks_skips_empty_and_blank_fields():
    df = make_clean_df(
        [
            clean_row(
                efficacy="감기에 사용합니다.",
                usage="",
                precaution="   ",
                interaction="다른 약과 함께 복용하지 마세요.",
            )
        ]
    )
    chunks = build_chunks(df)
    fields = {c["metadata"]["field"] for c in chunks}
    assert fields == {"efficacy", "interaction"}
    assert len(chunks) == 2


def test_build_chunks_id_and_text_format():
    df = make_clean_df([clean_row(efficacy="소화불량에 사용합니다.")])
    chunks = build_chunks(df)
    assert len(chunks) == 1
    chunk = chunks[0]
    assert chunk["id"] == "195700020_efficacy"
    assert chunk["text"] == "활명수의 효능: 소화불량에 사용합니다."
    assert chunk["metadata"] == {
        "item_seq": "195700020",
        "item_name": "활명수",
        "company": "동화약품(주)",
        "field": "efficacy",
    }


def test_build_chunks_no_id_collision_across_items():
    df = make_clean_df(
        [
            clean_row(item_seq="1", item_name="A", company="C1", efficacy="효능A"),
            clean_row(item_seq="2", item_name="B", company="C2", efficacy="효능B"),
        ]
    )
    chunks = build_chunks(df)
    ids = [c["id"] for c in chunks]
    assert len(ids) == len(set(ids)) == 2