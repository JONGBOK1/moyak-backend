from src.api.routes.drugs import DrugSummary, search_drugs

DRUGS = [
    DrugSummary(item_seq="1", item_name="타이레놀정500mg", company="한국얀센"),
    DrugSummary(item_seq="2", item_name="게보린정", company="삼진제약"),
    DrugSummary(item_seq="3", item_name="어린이타이레놀시럽", company="한국얀센"),
]


def test_search_drugs_matches_substring():
    results = search_drugs(DRUGS, "타이레놀")
    assert {d.item_seq for d in results} == {"1", "3"}


def test_search_drugs_empty_query_returns_nothing():
    assert search_drugs(DRUGS, "") == []
    assert search_drugs(DRUGS, "   ") == []


def test_search_drugs_no_match_returns_empty_list():
    assert search_drugs(DRUGS, "존재하지않는약품") == []


def test_search_drugs_respects_limit():
    results = search_drugs(DRUGS, "정", limit=1)
    assert len(results) == 1
