import pandas as pd

from src.ingestion.clean import clean, strip_html


def make_raw_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def raw_row(**overrides) -> dict:
    row = {
        "itemSeq": "100",
        "itemName": "테스트약",
        "entpName": "테스트제약",
        "efcyQesitm": "효능 내용",
        "useMethodQesitm": "사용법 내용",
        "atpnWarnQesitm": None,
        "atpnQesitm": None,
        "intrcQesitm": None,
        "seQesitm": None,
        "depositMethodQesitm": None,
        "openDe": "20200101",
        "updateDe": "2020-01-01",
        "itemImage": "",
        "bizrno": "123",
    }
    row.update(overrides)
    return row


def test_strip_html_removes_tags_and_collapses_whitespace():
    assert strip_html("<b>효능</b>  설명입니다.   ") == "효능 설명입니다."


def test_strip_html_handles_non_string():
    assert strip_html(None) == ""
    assert strip_html(float("nan")) == ""


def test_clean_removes_duplicates_keeping_more_complete_record():
    df = make_raw_df(
        [
            raw_row(itemImage=""),
            raw_row(itemImage="http://example.com/image.png"),
        ]
    )
    result = clean(df)
    assert len(result) == 1
    assert result.iloc[0]["item_image"] == "http://example.com/image.png"


def test_clean_strips_html_tags_from_text_fields():
    df = make_raw_df([raw_row(itemSeq="200", efcyQesitm="<p>효능 <b>설명</b></p>")])
    result = clean(df)
    assert result.iloc[0]["efficacy"] == "효능 설명"


def test_clean_id_columns_are_strings():
    df = make_raw_df([raw_row(itemSeq="300")])
    result = clean(df)
    row = result.iloc[0]
    assert isinstance(row["item_seq"], str)
    assert isinstance(row["biz_no"], str)
    assert isinstance(row["open_date"], str)


def test_clean_keeps_distinct_items_separate():
    df = make_raw_df([raw_row(itemSeq="400"), raw_row(itemSeq="500")])
    result = clean(df)
    assert sorted(result["item_seq"]) == ["400", "500"]