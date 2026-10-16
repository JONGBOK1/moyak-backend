"""알약 각인 비교 규칙 테스트 (DB·사진·API 호출 없음).

여기 있는 건 전부 **실제로 틀렸던 것**을 막는 테스트다. 각인 비교를 조금만 건드려도
검색 정확도가 눈에 띄게 떨어지므로, 고치기 전에 이 테스트부터 돌릴 것.
"""

from src.pills.ocr import _clean
from src.pills.search import _clean_imprint, _close, _tokens


# --- DB 각인 다듬기 -------------------------------------------------
# drug_pills.print_front 에는 글자가 아닌 표시가 적혀 있다 — '마크'(회사 로고) 2,600건,
# '분할선'(쪼개는 홈) 2,032건. OCR 은 이걸 읽을 수 없으므로 양쪽에서 똑같이 지워야 비교가 맞는다.

def test_clean_imprint_drops_non_letter_markers():
    assert _clean_imprint("마크CT") == "CT"
    assert _clean_imprint("분할선") == ""


def test_clean_imprint_drops_separators():
    # 정답 'D-W' 를 OCR 은 'DW' 로 읽는다. 맞은 건데 틀렸다고 세면 안 된다.
    assert _clean_imprint("D-W") == "DW"
    assert _clean_imprint("A B.C/D+E") == "ABCDE"


def test_clean_imprint_is_case_insensitive():
    assert _clean_imprint("ctr") == "CTR"


def test_clean_imprint_handles_none():
    assert _clean_imprint(None) == ""


# --- 한 글자 오독 보정 ----------------------------------------------
# OCR 오답은 SSN→SSV, WIG→WLG, MBL→MSL 처럼 대부분 한 글자 차이였다.

def test_close_accepts_one_wrong_letter():
    assert _close("SSN", "SSV")


def test_close_accepts_one_missing_letter():
    assert _close("MPG", "MPGX")
    assert _close("MPGX", "MPG")


def test_close_rejects_two_differences():
    assert not _close("ABCD", "AXYD")


def test_close_rejects_short_imprints():
    # "AB" 와 거리 1 인 각인은 수백 개라 후보가 폭발한다. 3글자 미만은 아예 보지 않는다.
    assert not _close("AB", "AC")


def test_close_rejects_big_length_gap():
    assert not _close("ABC", "ABCDE")


# --- OCR 결과 다듬기 ------------------------------------------------

def test_clean_drops_mfds_watermark():
    # 식약처 사진에는 기관명이 같이 찍혀 있다. 각인으로 오인하면 안 된다.
    assert _clean("MFDS Ministry of Food and Drug Safety TYLENOL") == "TYLENOL"


def test_clean_drops_lone_digit_but_keeps_letter():
    # 한 글자 숫자는 그림자나 얼룩일 때가 많다. 영문·한글 한 글자는 각인일 수 있어 남긴다.
    assert _clean("8") == ""
    assert _clean("H") == "H"


def test_clean_keeps_hangul_imprints():
    # 일반의약품 각인의 18% 가 한글이다 ("깅코탄", "제로민" 등).
    assert _clean("깅코탄") == "깅코탄"


# --- OCR 결과를 토큰으로 쪼개기 ------------------------------------
# 여기 있는 건 전부 실제로 놓쳤던 사진이다. 쪼개는 방식을 건드리면 바로 티가 난다.

def test_tokens_keeps_hyphenated_imprint():
    # 'F-N' 을 ['F','N'] 으로 쪼개면 둘 다 1글자라 버려지고, 각인 조건이 통째로
    # 빠져 후보 30개 안에 정답이 안 들어왔다(화콜노즈정). 기호는 지우고 나서 쪼갠다.
    # ('M' 은 1글자 토큰으로 따로 남는다 — 아래 test_tokens_keeps_single_letter 참고)
    assert "FN" in _tokens("F-N M")
    assert "F" not in _tokens("F-N M")


def test_tokens_still_splits_on_space():
    # 앞뒤 조각을 읽으면 서로 다른 각인이 공백으로 이어져 온다. 공백까지 지워
    # 'DWMPG' 로 만들면 그런 각인은 세상에 없어 0건이 된다.
    assert "DW" in _tokens("D-W MPG")
    assert "MPG" in _tokens("D-W MPG")


def test_tokens_adds_joined_form():
    # OCR 이 한 덩어리 각인에 공백을 끼워 넣는다 — 'TYPENER' → 'TYPEN ER'.
    # 붙인 형태가 있어야 부분일치(0.25)가 아니라 완전일치(0.45)를 받는다.
    assert "TYPENER" in _tokens("TYPEN ER")


def test_tokens_keeps_single_letter():
    # 각인이 한 글자뿐인 일반의약품이 95개 있다('V+' → 'V').
    # 버리면 완벽하게 읽고도 못 찾는다. 쓰는 쪽에서 완전일치만 인정한다.
    assert _tokens("V+") == ["V"]


def test_tokens_empty_input():
    assert _tokens(None) == []
    assert _tokens("  ") == []


# --- 그리스 문자 ----------------------------------------------------
# DB 가 라틴 대신 그리스 문자를 쓴 품목이 있다. 눈으로도 OCR 로도 구별이 안 된다.

def test_greek_letters_compare_as_latin():
    assert _clean_imprint("BPΛΛ") == "BPAA"       # 아니스펜8시간이알서방정
    assert _clean_imprint("αP") == "AP"           # 에스알파플러스정
    assert _clean_imprint("YPCΛ 100") == "YPCA100"  # 카로마스텐질정


def test_greek_normalisation_matches_ocr_output():
    # OCR 은 라틴으로 읽는다. 양쪽을 통일해야 같은 것으로 본다.
    assert _tokens("BPAA")[0] == _clean_imprint("BPΛΛ")
