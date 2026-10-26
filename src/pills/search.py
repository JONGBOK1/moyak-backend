"""사진에서 뽑은 모양·색·각인으로 `drug_pills` 에서 후보를 찾는다.

**완벽한 판별을 전제하지 않는다.** 실측 정확도는 모양 88%, 색 77% 라서
정확히 일치하는 것만 찾으면 1/4 은 놓친다. 그래서

  - 색은 **인접색까지** 함께 본다 (연두↔초록, 갈색↔빨강 같은 혼동이 대부분이다)
  - 모양도 혼동이 잦은 짝(타원형↔장방형)을 함께 본다
  - 대신 정확히 맞은 것에 높은 점수를 줘서 **순위로 가른다**

각인이 읽히면 후보가 급격히 줄어든다 — 실측상 모양+색은 평균 175개가 남지만
각인까지 맞추면 1.3개다. 그래서 각인이 있으면 그쪽에 큰 가중치를 둔다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

#: 실측에서 서로 헷갈린 색끼리 묶었다(scripts/check_pill_vision.py 오답 통계).
#: 판별이 틀려도 후보 목록에는 남게 하려는 것이다.
_COLOR_NEIGHBORS: dict[str, tuple[str, ...]] = {
    "빨강": ("주황", "갈색", "자주"),
    "주황": ("빨강", "갈색", "노랑", "분홍"),
    "갈색": ("빨강", "주황", "검정"),
    "노랑": ("주황", "연두", "하양"),
    "연두": ("노랑", "초록"),
    "초록": ("연두", "청록"),
    "청록": ("초록", "파랑"),
    "파랑": ("청록", "남색", "보라"),
    "남색": ("파랑", "보라", "검정"),
    "보라": ("파랑", "남색", "자주"),
    "자주": ("보라", "분홍", "빨강"),
    "분홍": ("빨강", "주황", "하양", "자주"),
    "하양": ("회색", "노랑", "분홍"),
    "회색": ("하양", "검정"),
    "검정": ("회색", "갈색", "남색"),
}

#: 후보 점수 배점. **손으로 고치지 말고 반드시 재고 나서 고칠 것** —
#: `python scripts/check_pill_search.py --n 120 --ocr google` 으로 전후를 비교한다.
#: (점수 계산만 바꾸면 OCR 캐시가 그대로 쓰여서 Google 호출 없이 즉시 돌아간다)
W_SHAPE = 0.30          # 모양이 정확히 같다
W_SHAPE_NEAR = 0.12     # 혼동이 잦은 이웃 모양
W_COLOR = 0.25          # 색이 정확히 같다
W_COLOR_NEAR = 0.10     # 혼동이 잦은 이웃 색
W_PRINT_EXACT = 0.45    # 각인이 통째로 같다 — 거의 확정
W_PRINT_PART = 0.25     # 각인 일부가 들어 있다
W_PRINT_NEAR = 0.15     # 한 글자 차이
W_PRINT_LEN = 0.04      # 각인 한 글자당 가산점 ('CT' 보다 'CRWSOB100' 이 확실하다)
W_PRINT_LEN_MAX = 0.12
W_PRINT_BOTH = 0.20     # 앞뒤 각인이 둘 다 통째로 같다
W_PRINT_ALL = 0.15      # 읽은 조각(2자 이상)이 전부 이 약의 각인 안에 있다
#   같은 119장, 1위 정답:  0 → 0.05 → 0.10 → 0.20
#     Google  67.2 → 72.3 → 72.3 → 74.8 %
#     LLM     68.9 → 71.4 → 72.3 → 77.3 %   (후보 안 정답은 0.05 부터 그대로)
W_ASPECT = 0.14         # 가로세로비가 거의 같다
W_ASPECT_NEAR = 0.07
W_LOGO = 0.20           # 사진에 로고가 있고, DB 각인에도 '마크' 가 있다
#   Claude 판정 기준 (후보 안 / 1위):        0           0.10        0.20        0.30
#     119장 (가중치를 고른 사진)          93.3/84.9   94.1/84.9   94.1/84.9   94.1/84.9
#     새 500장 (확인용)                  96.8/87.6   97.6/88.4   97.8/88.6   97.8/88.8
#   119장에선 0.10~0.30 이 똑같아 앞뒤 각인 가산점과 같은 0.20 으로 뒀다.
#   크게 안 오르는 이유: 로고 약끼리는 여전히 동점이다(삼각형·파랑·로고 약이 3개 등).
#   그걸 가르려면 로고 모양 자체를 비교해야 한다.

#: 한 후보가 받을 수 있는 최대 점수. 화면에 보여줄 때 이 값으로 나눠 0~100 으로 만든다.
MAX_SCORE = (W_SHAPE + W_COLOR + W_PRINT_EXACT + W_PRINT_LEN_MAX + max(W_PRINT_BOTH, W_PRINT_ALL)
             + W_ASPECT + W_LOGO)


#: 각인에 그리스 문자를 쓴 품목이 있다. 눈으로는 라틴 문자와 구별이 안 되고 OCR 도
#: 라틴으로 읽는다 — 실측에서 'BPΛΛ'→'BPAA', 'αP'→'AP', 'YPCΛ 100'→'YPCA 100' 으로
#: 3건을 놓쳤다. OCR 이 틀린 게 아니라 DB 가 다른 글자를 쓴 것이므로, 양쪽을 라틴으로
#: 통일해서 비교한다. (Λ 는 가로획 없는 A 라 A 로 본다)
#:
#: 소문자(α 등)는 upper() 가 먼저 대문자로 바꾸므로 대문자만 적으면 된다.
_GREEK = "ΑΒΕΖΗΙΚΜΝΟΡΤΥΧΛ"
_LATIN = "ABEZHIKMNOPTYXA"
_GREEK_TABLE = str.maketrans(_GREEK, _LATIN)


def _to_latin(t: str) -> str:
    """그리스 문자를 똑같이 생긴 라틴 문자로 바꾼다. 반드시 upper() 뒤에 쓴다."""
    return t.translate(_GREEK_TABLE)


#: DB 의 각인을 비교용으로 다듬는 SQL 조각.
#:
#: 두 가지를 없앤다 — 실측에서 이것 때문에 놓친 게 3건이었다:
#:   1) '마크'(회사 로고)·'분할선'(쪼개는 홈)  — 글자가 아닌데 각인 칸에 적혀 있다.
#:      정답이 '마크CT' 인데 OCR 은 'MCT' 를 읽는다. 그대로 비교하면 안 맞는다.
#:   2) 공백·하이픈·점  — 정답 'D-W' 를 OCR 은 'DW' 로 읽는다. 사실 맞은 건데 틀렸다고 센다.
_CLEAN_SQL = (
    "translate("
    "  upper(translate(replace(replace(coalesce({col}, ''), '마크', ''), '분할선', ''),"
    "                  ' -./+', ''))"
    ", '" + _GREEK + "', '" + _LATIN + "')"
)

#: 모양도 헷갈리는 짝이 있다. 장방형↔타원형이 11건으로 가장 많았다.
_SHAPE_NEIGHBORS: dict[str, tuple[str, ...]] = {
    "원형": ("타원형", "팔각형", "육각형"),
    "타원형": ("장방형", "원형"),
    "장방형": ("타원형",),
    "사각형": ("장방형", "마름모형"),
    "마름모형": ("사각형",),
    "삼각형": ("원형",),
    "오각형": ("육각형", "원형"),
    "육각형": ("팔각형", "원형"),
    "팔각형": ("육각형", "원형"),
}


#: 각인에서 서로 헷갈리는 숫자와 글자. 비교할 때만 한쪽으로 모은다.
#: 실측(폰 사진 흉내): 'IB' 를 '1B', 'BZ' 를 'B2' 로 읽었다. 두 글자라 한 글자 차이
#: 허용(3자 이상만)에도 안 걸려서 못 찾았다.
_CONFUSE_FROM, _CONFUSE_TO = "01258", "OIZSB"
_CONFUSE = str.maketrans(_CONFUSE_FROM, _CONFUSE_TO)


def _canon(t: str) -> str:
    return t.translate(_CONFUSE)


def _clean_imprint(s: str | None) -> str:
    """DB 각인을 비교용으로 다듬는다. `_CLEAN_SQL` 과 똑같이 맞춰야 한다.

    '마크'·'분할선' 은 글자가 아니라 표시이고, 공백·하이픈은 OCR 이 흘려 읽는다.
    """
    t = _to_latin((s or "").upper()).replace("마크", "").replace("분할선", "")
    return re.sub(r"[ \-./+]", "", t)


def _parts(print_text: str | None) -> list[str]:
    """읽은 글자를 공백 등에서 쪼갠 조각. `_tokens` 의 앞부분이다(붙여 만든 것은 없다)."""
    text_ = _to_latin((print_text or "").upper())
    text_ = re.sub(r"[\-./+]", "", text_)
    return [t for t in re.split(r"[^A-Z0-9가-힣]+", text_) if t]


def _tokens(print_text: str | None) -> list[str]:
    """OCR 이 읽은 글자를 DB 각인과 비교할 토큰으로 쪼갠다.

    **구분기호를 먼저 지우고 나서 쪼갠다.** 순서를 거꾸로 하면 하이픈이 든 각인이
    통째로 사라진다 — 실측에서 'F-N M' 이 ['F','N','M'] 으로 쪼개져 전부 1글자로
    버려졌고, 각인 조건 자체가 빠져서 후보 30개 안에 정답이 안 들어왔다.
    DB 쪽(`_CLEAN_SQL`)도 같은 기호를 지우므로 양쪽을 맞춰야 한다.

    **공백은 지우지 않는다.** 알약 하나에 'D-W' 와 'MPG' 처럼 떨어진 각인이 있고
    앞뒤 조각을 읽으면 둘이 공백으로 이어져 넘어온다. 공백까지 지워 'DWMPG' 로
    만들면 그런 각인은 세상에 없어서 0건이 된다.
    """
    text_ = _to_latin((print_text or "").upper())
    # 구분기호는 **지우고**(쪼개지 말고), 그 밖의 기호에서만 쪼갠다.
    text_ = re.sub(r"[\-./+]", "", text_)
    # 1글자도 버리지 않는다. 각인이 한 글자뿐인 일반의약품이 95개 있고('V+' → 'V'),
    # 버리면 완벽하게 읽고도 못 찾는다. 대신 `find` 에서 **완전히 같을 때만** 쓴다 —
    # 부분일치로 쓰면 'M' 하나가 수백 개와 맞아 후보가 터진다.
    out = [t for t in re.split(r"[^A-Z0-9가-힣]+", text_) if t]
    parts = list(out)

    # **붙여 쓴 형태도 후보로 넣는다.** OCR 은 한 덩어리 각인에 공백을 끼워 넣을 때가
    # 있다 — 실측에서 'TYPENER' 를 'TYPEN ER' 로 읽어, 부분일치(0.25)밖에 못 받고
    # 순위 밖으로 밀렸다. 붙인 'TYPENER' 가 맞으면 완전일치(0.45)가 된다.
    # 너무 길면 실제 각인일 리 없고 조회만 무거워지므로 12자까지만.
    long_ = [t for t in out if len(t) >= 2]
    if len(long_) >= 2:
        joined = "".join(long_)
        if len(joined) <= 12 and joined not in out:
            out.append(joined)

    # **이웃한 두 조각도 붙여 본다 — 한 글자 조각까지.** 분할선 양쪽 글자는 떨어져
    # 읽힌다: DB 'A분할선C'('AC'), 'M분할선Z'('MZ') 를 'A C', 'M Z D' 로 읽는다.
    # 위의 '전부 붙이기' 는 한 글자를 빼고 붙여서 이걸 못 만든다. 실측에서 각인을 맞게
    # 읽고도 이 때문에 순위 밖으로 밀린 게 비전 LLM 기준 3건이었다.
    for a, b in zip(parts, parts[1:]):
        pair = a + b
        if len(pair) <= 12 and pair not in out:
            out.append(pair)
    return out


def _close(a: str, b: str) -> bool:
    """글자 하나 차이인지. OCR 오독을 보정하려고 쓴다.

    3글자 미만에는 쓰지 않는다 — "AB" 와 거리 1 인 각인은 수백 개라 의미가 없다.
    """
    if not a or not b or len(a) < 3 or abs(len(a) - len(b)) > 1:
        return False
    if a == b:
        return True
    # 길이가 같으면 다른 글자 수를 센다
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    # 길이가 1 다르면 긴 쪽에서 한 글자를 빼 본다
    long, short = (a, b) if len(a) > len(b) else (b, a)
    return any(long[:i] + long[i + 1:] == short for i in range(len(long)))


@dataclass
class Candidate:
    item_seq: str
    item_name: str
    entp_name: str | None
    drug_shape: str | None
    color_class1: str | None
    print_front: str | None
    print_back: str | None
    item_image: str | None
    etc_otc_name: str | None
    #: 0~1. 모양·색·각인이 얼마나 맞았는지.
    score: float
    #: 어디가 맞았는지 — 화면에 "각인 일치" 같은 설명을 띄울 때 쓴다.
    matched: list[str]


def find(
    # 읽기 전용 Connection 으로도 부를 수 있다 — 팀 Supabase 는 절대 쓰지 않는 게 원칙이라
    # 라우터는 `SET TRANSACTION READ ONLY` 를 건 Connection 을 넘긴다.
    session: Session | Connection,
    *,
    shape: str | None,
    color: str | None,
    print_text: str | None = None,
    aspect: float | None = None,
    has_logo: bool | None = None,
    otc_only: bool = True,
    limit: int = 20,
) -> list[Candidate]:
    """후보를 점수순으로 돌려준다.

    `otc_only` 가 참이면 일반의약품만 본다. 자판기에서 파는 건 일반의약품뿐이고,
    후보가 25,350개에서 5,587개로 줄어 정확도가 크게 오른다.

    `has_logo` 는 비전 LLM 이 "글자가 아닌 로고가 새겨져 있다" 고 본 것이다
    (`vision_llm`). 참이면 DB 각인에 '마크' 가 있는 약에 가산점을 준다.
    """
    shapes = [shape] + list(_SHAPE_NEIGHBORS.get(shape or "", ())) if shape else []
    colors = [color] + list(_COLOR_NEIGHBORS.get(color or "", ())) if color else []
    # 각인은 **토큰 단위**로 다룬다. 알약 하나에 'D-W' 와 'MPG' 처럼 떨어진 각인이
    # 있을 수 있고, 앞뒤 두 조각을 읽으면 둘이 합쳐져 넘어온다.
    # 이걸 공백만 지워 'D-WMPG' 로 조회하면 그런 각인은 세상에 없어서 0건이 된다.

    print_tokens = _tokens(print_text)
    # 한 글자 조각도 넣는다 — 'PK분할선H' 를 'H PK' 로 순서를 바꿔 읽었을 때 'H' 까지 봐야 잡힌다.
    # 대신 전부 합쳐 3자 이상일 때만 쓴다('A B' 처럼 짧으면 우연히 다 들어 있는 약이 너무 많다).
    read_parts = _parts(print_text)
    if sum(map(len, read_parts)) < 3:
        read_parts = []

    sql = """
        SELECT item_seq, item_name, entp_name, drug_shape, color_class1,
               print_front, print_back, item_image, etc_otc_name,
               leng_long, leng_short
          FROM drug_pills
         WHERE item_image IS NOT NULL
    """
    params: dict = {}
    if otc_only:
        sql += " AND etc_otc_name = '일반의약품'"
    # **각인을 읽었으면 모양·색으로도 거르지 않는다** (아래 비율과 같은 이유).
    # 각인을 맞게 읽고도 모양·색이 이웃 범위 밖으로 틀리면 정답이 SQL 에서 통째로 빠진다 —
    # 비전 LLM 으로 재보니 놓친 20건 중 9건이 "각인은 맞음 · 모양/색 틀림" 이었다
    # (연질캡슐 갈색→자주, 삼각형→원형 등). 거르지 않아도 점수에서 모양·색 가산점은
    # 그대로 주므로 맞는 쪽이 위로 온다. 같은 119장(이웃 토큰 붙이기 포함):
    #   Google  후보 안 81.5% → 85.7% · 1위 64.7% → 67.2% · 5위 78.2% → 82.4%
    #   LLM     후보 안 83.2% → 93.3% · 1위 63.9% → 68.9% · 5위 78.2% → 86.6%
    # 대가는 후보 수(평균 12개 → 21개)다. 각인이 걸러 주므로 조회 결과는 300행을 안 넘었다.
    if shapes and not print_tokens:
        sql += " AND drug_shape = ANY(:shapes)"
        params["shapes"] = shapes
    if colors and not print_tokens:
        # color_class1 은 "노랑, 투명" 처럼 복합값이 올 수 있어 앞글자로 맞춘다.
        sql += " AND (color_class1 = ANY(:colors) OR split_part(color_class1, ',', 1) = ANY(:colors))"
        params["colors"] = colors
    # **각인을 읽었으면 비율로 거르지 않는다.** 비율은 각인이 없을 때 후보를 줄이려고
    # 넣은 조건인데, 각인이 있으면 이미 충분히 좁혀져 있고 비율은 놓칠 위험만 남는다 —
    # 실측에서 콜프리캡슐은 각인('콜프리')을 완벽히 읽고도 비율(DB 1.88 vs 인식 2.52)
    # 때문에 SQL 에서 통째로 빠졌다. 보는 각도에 따라 비율은 쉽게 틀어진다.
    # 거르지 않아도 아래 점수 계산에서 비율이 맞으면 가산점은 그대로 준다.
    if aspect and aspect > 0 and not print_tokens:
        # 가로세로비로 좁힌다. **길이가 아니라 비율**이라 사진 배율과 무관하다 —
        # 폰으로 찍은 사진에는 기준자가 없어 실제 mm 는 못 재지만 비율은 잴 수 있다.
        # 실측상 후보가 평균 53.2개 → 11.4개로 줄었다.
        # 범위를 넉넉히 두는 건 보는 각도에 따라 비율이 달라지기 때문이다.
        sql += """ AND (leng_long IS NULL OR leng_short IS NULL OR leng_short = 0
                     OR leng_long / leng_short BETWEEN :amin AND :amax)"""
        params["amin"] = max(1.0, aspect - 0.45)
        params["amax"] = aspect + 0.45

    if print_tokens:
        # 토큰 중 **하나라도** 맞으면 후보로 본다. 전부 맞으라고 하면,
        # OCR 이 한 조각만 제대로 읽어도 탈락해 버린다.
        #
        # **글자 하나 틀린 것도 받아준다.** OCR 은 비슷한 글자를 자주 바꿔 읽는다 —
        # 실측 오답이 SSN→SSV, WIG→WLG, MBL→MSL 처럼 대부분 한 글자 차이였다.
        # levenshtein 은 fuzzystrmatch 확장이 제공한다(sql/015 참고).
        ors = []
        # **긴 토큰부터 쓴다.** 조회 조건은 다섯 개까지만 넣는데, 읽은 순서대로
        # 자르면 'M' 'H' 같은 쓸모없는 조각이 자리를 차지하고 정작 'LITHIUM' 이
        # 잘려 나간다. 긴 각인일수록 겹치는 약이 적어 확실하다.
        for i, tok in enumerate(sorted(print_tokens, key=len, reverse=True)[:5]):
            f_ = _CLEAN_SQL.format(col="print_front")
            b_ = _CLEAN_SQL.format(col="print_back")
            params[f"tok{i}"] = tok
            if len(tok) == 1:
                # 한 글자는 **각인 전체가 그 글자인 약**만 본다. 부분일치를 허용하면
                # 'M' 하나가 수백 개와 맞는다.
                ors.append(f"({f_} = :tok{i} OR {b_} = :tok{i})")
                continue
            conf = f"'{_CONFUSE_FROM}', '{_CONFUSE_TO}'"
            ors.append(f"""(
                  {f_} LIKE :pat{i} OR {b_} LIKE :pat{i}
               OR levenshtein({f_}, :tok{i}) <= :d{i}
               OR levenshtein({b_}, :tok{i}) <= :d{i}
               OR translate({f_}, {conf}) = :canon{i}
               OR translate({b_}, {conf}) = :canon{i}
            )""")
            params[f"pat{i}"] = f"%{tok}%"
            # 짧은 각인에 거리 1 을 허용하면 후보가 폭발한다("AB" 는 수백 개와 거리 1).
            params[f"d{i}"] = 1 if len(tok) >= 3 else 0
            # 헷갈리는 글자(1/I, 2/Z ...)를 한쪽으로 모은 뒤 통째로 같으면 후보로 넣는다.
            params[f"canon{i}"] = _canon(tok)
        sql += " AND (" + " OR ".join(ors) + ")"

    # 후보를 넉넉히 가져와 파이썬에서 점수를 매긴다.
    # **정렬 없이 자르면 안 된다** — PostgreSQL 이 아무 순서로나 주기 때문에
    # 정답이 잘려 나갈 수 있다. 각인이 있는 것부터 가져오면 쓸모 있는 쪽이 남는다.
    # 로고를 봤으면 '마크' 가 있는 약부터 가져온다 — 1200 에서 잘리더라도 그쪽이 남게.
    logo_first = ("(coalesce(print_front, '') LIKE '%마크%' OR coalesce(print_back, '') LIKE '%마크%') DESC,"
                  if has_logo else "")
    sql += f""" ORDER BY {logo_first}
                        (print_front IS NOT NULL AND print_front <> '') DESC,
                        item_seq
               LIMIT 1200"""
    rows = session.execute(text(sql), params).mappings().all()

    # **각인으로 하나도 못 찾았으면 각인을 빼고 다시 찾는다.** 각인이 있으면 모양·색으로
    # 거르지 않으므로(위 참고) 각인을 잘못 읽으면 후보가 통째로 0개가 된다 — 실제로
    # 나무 위 원형 알약의 로고를 '9L' 로 읽어 빈 목록이 나왔다. 빈 화면보다는
    # 모양·색으로 찾은 후보를 보여주는 게 낫다. 각인이 맞은 후보가 없다는 건
    # 호출한 쪽에서 `matched` 에 '각인' 이 없는 것으로 알 수 있다.
    if print_tokens and not rows:
        return find(session, shape=shape, color=color, print_text=None, aspect=aspect,
                    has_logo=has_logo, otc_only=otc_only, limit=limit)

    out: list[tuple[float, Candidate]] = []
    for r in rows:
        score, matched = 0.0, []
        if shape and r["drug_shape"] == shape:
            score += W_SHAPE
            matched.append("모양")
        elif shape and r["drug_shape"] in _SHAPE_NEIGHBORS.get(shape, ()):
            score += W_SHAPE_NEAR

        row_color = (r["color_class1"] or "").split(",")[0].strip()
        if color and row_color == color:
            score += W_COLOR
            matched.append("색")
        elif color and row_color in _COLOR_NEIGHBORS.get(color, ()):
            score += W_COLOR_NEAR

        if print_tokens:
            front = _clean_imprint(r["print_front"])
            back = _clean_imprint(r["print_back"])
            # 토큰마다 가장 좋은 일치를 찾아, 그중 최고 점수를 준다.
            best = 0.0
            label = ""
            for tok in print_tokens:
                if tok in (front, back):
                    cur, lab = W_PRINT_EXACT, "각인 일치"       # 통째로 같다 — 거의 확정
                elif len(tok) == 1:
                    continue                          # 한 글자는 완전일치만 인정한다
                elif front and tok in front or back and tok in back:
                    # **얼마나 덮었는지에 따라 준다.** 'KJCX10' 은 'KJCX100' 의 7자 중 6자라
                    # 거의 맞은 것인데, 고정 점수(0.25)를 주면 짧은 'KJ' 가 통째로 맞은 다른 약
                    # (0.45)에 진다 — 실사진에서 셀렉시브캡슐이 그래서 10위 밖이었다.
                    # 짧은 조각('AC' 가 'ACT' 의 2/3)까지 후하게 치면 식약처 500장에서 1위가
                    # 2장 줄었다. 4자 이상 읽었을 때만 덮은 비율을 쓴다.
                    cover = (max(len(tok) / len(f) for f in (front, back) if f and tok in f)
                             if len(tok) >= 4 else 0.0)
                    cur = W_PRINT_PART + (W_PRINT_EXACT - W_PRINT_PART) * cover * cover
                    lab = "각인 부분일치"
                elif _close(tok, front) or _close(tok, back):
                    cur, lab = W_PRINT_NEAR, "각인 비슷함"     # 한 글자 차이
                elif _canon(tok) in (_canon(front), _canon(back)):
                    cur, lab = W_PRINT_NEAR, "각인 비슷함"     # 1/I, 2/Z 같은 헷갈리는 글자
                else:
                    continue
                # **긴 각인일수록 확실하다.** 'CT' 는 수십 개 약과 겹치지만
                # 'CRWSOB100' 이 맞으면 그 약 하나다. 길이에 따라 가산점을 준다.
                # ('마크' 를 지우면 각인이 짧아져 1위 정확도가 50% → 45% 로 떨어졌다)
                cur += min(W_PRINT_LEN_MAX, max(0, len(tok) - 2) * W_PRINT_LEN)
                if cur > best:
                    best, label = cur, lab
            # **앞뒤 각인이 둘 다 통째로 맞으면 더 준다.** 회사 표시는 여러 약에 똑같이
            # 찍힌다 — 'KPP' 는 일반의약품 60개에 있다. 토큰 하나의 최고점만 보면
            # 'KPP VIC' 를 정확히 읽고도 'KPP' 만 맞은 59개와 동점이 되어, 실측에서
            # 파워빅타민정이 30위 밖으로 밀렸다.
            if (W_PRINT_BOTH and front and back and front != back
                    and front in print_tokens and back in print_tokens):
                best, label = best + W_PRINT_BOTH, "각인 앞뒤 일치"
            # **읽은 조각이 전부 이 약에 들어 있으면 더 준다(순서는 상관없다).** 'CA MM' 을 읽었을 때 정답
            # 'CA분할선MM' 은 둘 다 들어 있지만, 'CA' 하나만 맞은 약·'MM' 하나만 맞은 약과
            # 점수가 거의 같아서 33위로 밀렸다(폰 사진 흉내).
            elif W_PRINT_ALL and len(read_parts) >= 2 and all(
                    p_ in front or p_ in back for p_ in read_parts):
                best, label = best + W_PRINT_ALL, "각인 전부 일치"
            if best:
                score += best
                matched.append(label)

        # **로고를 봤으면 '마크' 가 있는 약에 더 준다.** 일반의약품의 약 13% 는 각인이 글자가
        # 아니라 회사 로고라 각인으로는 못 찾고, 모양·색이 같은 수십 개와 동점이 된다.
        # 새 사진 500장에서 놓친 16건 중 13건이 이 부류였고 그중 10건은 LLM 이 로고라고 답했다.
        # 로고 모양까지 맞추는 건 아니므로 "로고가 있는 약" 이라는 것만 가른다.
        if has_logo and ("마크" in (r["print_front"] or "") or "마크" in (r["print_back"] or "")):
            score += W_LOGO
            matched.append("로고")

        if aspect and r["leng_long"] and r["leng_short"]:
            # 비율은 **거르는 조건이 아니라 점수**다(위 WHERE 참고). 거르면 각도 때문에
            # 정답이 통째로 빠지고, 점수로만 쓰면 순위만 내려간다. 다만 각인을 읽었을 때
            # 비율 조건을 뺀 만큼 후보가 늘어나므로(9.1개 → 12.6개) 가중치를 올려
            # 순위를 되돌린다. 가까울수록 더 주는 단계식이다.
            try:
                diff = abs(float(r["leng_long"]) / float(r["leng_short"]) - aspect)
                if diff <= 0.2:
                    score += W_ASPECT
                    matched.append("비율")
                elif diff <= 0.5:
                    score += W_ASPECT_NEAR
            except ZeroDivisionError:
                pass

        out.append((score, Candidate(
            item_seq=r["item_seq"], item_name=r["item_name"], entp_name=r["entp_name"],
            drug_shape=r["drug_shape"], color_class1=r["color_class1"],
            print_front=r["print_front"], print_back=r["print_back"],
            item_image=r["item_image"], etc_otc_name=r["etc_otc_name"],
            score=0.0, matched=matched,
        )))

    # **자르기 전의 점수로 정렬한다.** 다 더하면 1.26 까지 나오는데 1.0 으로 먼저
    # 자르고 정렬하면 상위권이 전부 동점이 되고, 순위가 이름순으로 정해져 버린다 —
    # 실제 사용자 사진에서 정답(클로틴정)이 1.0 동점 셋 중 이름순 3위로 밀렸다.
    # 자르는 것은 화면에 숫자를 보여줄 때만 한다.
    out.sort(key=lambda t: (-t[0], t[1].item_name))
    picked = out[:limit]
    for raw, c in picked:
        # **자르지 않고 만점 대비 비율로 바꾼다.** 1.0 에서 자르면 근거가 다른 후보들이
        # 전부 100 으로 같아져 화면에서 구별이 안 된다 — 실제로 1~3위가 모두 100 이면서
        # 색도 각인도 다른 약이었다. 만점(모양+색+각인+길이+비율)으로 나누면
        # "가능한 근거 중 얼마나 맞았나" 가 되고 순위도 그대로 보인다.
        c.score = round(min(1.0, raw / MAX_SCORE), 3)
    return [c for _, c in picked]
