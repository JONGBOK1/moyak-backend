from src.rag import drug_facts as df
from src.rag.drug_facts import DrugFacts

CONFLICTS = [
    ("D000027", "D000762", "심바스타틴", "이트라코나졸", "횡문근융해증"),
    ("D000100", "D000200", "시프로플록사신염산염", "티자니딘염산염", "티자니딘 혈중농도 증가"),
]


def test_normalize_strips_salts_but_keeps_core_name():
    assert df.normalize_ingredient("염산시프로플록사신") == "시프로플록사신"
    assert df.normalize_ingredient("시프로플록사신염산염") == "시프로플록사신"
    assert df.normalize_ingredient("수마트립탄숙신산염") == "수마트립탄"
    assert df.normalize_ingredient("이트라코나졸고체분산체") == "이트라코나졸"
    assert df.normalize_ingredient("니코틴산아미드") == "니코틴산아미드"


def test_same_ingredient_requires_exact_match_after_normalizing():
    assert df._same_ingredient("염산시프로플록사신", "시프로플록사신염산염")
    assert not df._same_ingredient("니코틴", "니코틴산아미드")  # 부분 일치로 잘못된 금기 경고를 만들지 않음


def test_parse_main_ingredients():
    assert df.parse_main_ingredients("[M040353]아세트아미노펜|[M0001]카페인무수물") == ["아세트아미노펜", "카페인무수물"]
    assert df.parse_main_ingredients(None) == []


def test_find_conflicts_by_dur_code_and_by_name():
    by_code = [DrugFacts("1", "심바스타틴정", dur_codes={"D000027"}), DrugFacts("2", "이트라코나졸캡슐", dur_codes={"D000762"})]
    assert [c.reason for c in df.find_conflicts(by_code, CONFLICTS)] == ["횡문근융해증"]

    by_name = [DrugFacts("3", "티자니딘정", ingredients=["티자니딘염산염"]), DrugFacts("4", "시프로정", ingredients=["염산시프로플록사신"])]
    found = df.find_conflicts(by_name, CONFLICTS)
    assert len(found) == 1 and found[0].reason == "티자니딘 혈중농도 증가"


def test_find_conflicts_none_for_unrelated_drugs():
    drugs = [DrugFacts("5", "타이레놀정", ingredients=["아세트아미노펜"]), DrugFacts("6", "베아제정", ingredients=["판크레아틴"])]
    assert df.find_conflicts(drugs, CONFLICTS) == []


def test_format_block_marks_population_and_never_says_safe():
    drug = DrugFacts("7", "약A", etc_otc="일반의약품", ingredients=["성분A"],
                     warnings=[{"type": "효능군중복", "ingredient": "성분A", "content": ""},
                               {"type": "임부금기", "ingredient": "성분A", "content": "태아 위험"}])
    other = DrugFacts("8", "약B", ingredients=["성분B"])
    block = df.format_block([drug, other], [], population="pregnant", check_pairs=True)
    lines = block.splitlines()
    assert "DUR 임부금기 ★질문 대상자 관련" in lines[2]  # 질문 대상자 관련 경고가 먼저
    assert "병용금기 목록에서 확인되지 않았습니다" in block
    assert "안전하다는 뜻은 아닙니다" in block


def test_build_enrichment_is_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("DRUG_DB_ENRICH", raising=False)
    assert df.build_enrichment(["202106092"], ["타이레놀"], check_pairs=True) == ("", [])


def test_build_enrichment_swallows_db_errors(monkeypatch):
    monkeypatch.setenv("DRUG_DB_ENRICH", "1")

    def boom(*a, **k):
        raise RuntimeError("db down")

    monkeypatch.setattr(df, "fetch_facts", boom)
    assert df.build_enrichment(["1"]) == ("", [])  # 실패해도 챗봇은 기존처럼 답변
