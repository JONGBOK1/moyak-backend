"""증상 추천 답변을 약별 카드(recommendations)로 나누는 파서 — API 호출 없음 (2026-10-19 챗봇 시인성 강화)."""
from src.rag.chain import parse_recommendations

ANSWER = """말씀하신 증상에 도움이 될 수 있는 약을 2가지 알려드릴게요.

💊 1. 타이레놀정500밀리그람(아세트아미노펜)
· 추천 이유: 두통과 발열을 줄여 줍니다.
· 성분: 아세트아미노펜이 열과 통증을 낮춥니다.
· 주의: 술과 함께 드시지 마세요.

💊 2. 베아제정
· 추천 이유: 소화불량에 씁니다.
· 주의: 다른 소화제와 같이 드시지 마세요.

복용법이나 더 자세한 주의사항이 궁금한 약을 말씀해 주시면 자세히 알려드릴게요.
정확한 진단과 복용은 약사와 상담하시기 바랍니다.
[참고: 타이레놀정500밀리그람(아세트아미노펜), 베아제정]"""


def test_splits_each_recommended_drug_into_a_card():
    cards = parse_recommendations(ANSWER)
    assert [c["item_name"] for c in cards] == ["타이레놀정500밀리그람(아세트아미노펜)", "베아제정"]
    assert cards[0] == {"rank": 1, "item_name": "타이레놀정500밀리그람(아세트아미노펜)", "reason": "두통과 발열을 줄여 줍니다.",
                        "ingredient": "아세트아미노펜이 열과 통증을 낮춥니다.", "caution": "술과 함께 드시지 마세요."}
    assert cards[1]["ingredient"] == ""  # 성분 줄이 없으면 빈 값
    assert "약사와 상담" not in cards[1]["caution"]  # 마무리 문장은 카드에 섞이지 않음


def test_non_recommendation_answer_has_no_cards():
    assert parse_recommendations("제공된 자료에서 확인되지 않습니다. 약사와 상담하시는 것을 권장드립니다.") == []
    assert parse_recommendations("") == []


def test_heading_without_number_still_splits():
    text = ANSWER.replace("💊 1. ", "💊 ").replace("💊 2. ", "💊 ")
    cards = parse_recommendations(text)
    assert [(c["rank"], c["item_name"]) for c in cards] == [(1, "타이레놀정500밀리그람(아세트아미노펜)"), (2, "베아제정")]
