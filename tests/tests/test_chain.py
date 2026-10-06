from langchain_core.documents import Document

from src.rag.chain import _extract_cited


def _doc(item_name: str, field: str = "side_effect", text: str = "내용") -> Document:
    return Document(page_content=text, metadata={"item_name": item_name, "field": field})


def test_extract_cited_matches_name_with_trailing_ingredient_parens_omitted():
    # LLM이 "[참고: ...]"에서 끝의 성분명 괄호를 생략하고 인용하는 경우가 실제로 있었다
    # (예: "타이레놀정500밀리그람(아세트아미노펜)" -> "타이레놀정500밀리그람").
    doc = _doc("타이레놀정500밀리그람(아세트아미노펜)")
    answer = "부작용 안내입니다. [참고: 타이레놀정500밀리그람]"

    sources, evidence = _extract_cited([doc], answer)

    assert sources == ["타이레놀정500밀리그람(아세트아미노펜)"]
    assert len(evidence) == 1


def test_extract_cited_matches_name_with_space_variation():
    doc = _doc("타이레놀정500밀리그람(아세트아미노펜)")
    answer = "[참고: 타이레놀정 500밀리그람]"

    sources, _ = _extract_cited([doc], answer)

    assert sources == ["타이레놀정500밀리그람(아세트아미노펜)"]


def test_extract_cited_excludes_uncited_docs():
    cited = _doc("타이레놀정500밀리그람(아세트아미노펜)")
    uncited = _doc("어린이타이레놀산160밀리그램(아세트아미노펜)")
    answer = "[참고: 타이레놀정500밀리그람]"

    sources, evidence = _extract_cited([cited, uncited], answer)

    assert sources == ["타이레놀정500밀리그람(아세트아미노펜)"]
    assert len(evidence) == 1