"""알약 각인 비교 규칙 테스트 (DB·사진·API 호출 없음).

여기 있는 건 전부 **실제로 틀렸던 것**을 막는 테스트다. 각인 비교를 조금만 건드려도
검색 정확도가 눈에 띄게 떨어지므로, 고치기 전에 이 테스트부터 돌릴 것.
"""

from src.pills.ocr import _clean
from src.pills.search import _clean_imprint, _close


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
