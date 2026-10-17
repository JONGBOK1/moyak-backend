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


def _clean_imprint(s: str | None) -> str:
    """DB 각인을 비교용으로 다듬는다. `_CLEAN_SQL` 과 똑같이 맞춰야 한다.

    '마크'·'분할선' 은 글자가 아니라 표시이고, 공백·하이픈은 OCR 이 흘려 읽는다.
    """
    t = _to_latin((s or "").upper()).replace("마크", "").replace("분할선", "")
    return re.sub(r"[ \-./+]", "", t)


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

    # **붙여 쓴 형태도 후보로 넣는다.** OCR 은 한 덩어리 각인에 공백을 끼워 넣을 때가
    # 있다 — 실측에서 'TYPENER' 를 'TYPEN ER' 로 읽어, 부분일치(0.25)밖에 못 받고
    # 순위 밖으로 밀렸다. 붙인 'TYPENER' 가 맞으면 완전일치(0.45)가 된다.
    # 너무 길면 실제 각인일 리 없고 조회만 무거워지므로 12자까지만.
    long_ = [t for t in out if len(t) >= 2]
    if len(long_) >= 2:
        joined = "".join(long_)
        if len(joined) <= 12 and joined not in out:
            out.append(joined)
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
    otc_only: bool = True,
    limit: int = 20,
) -> list[Candidate]:
    """후보를 점수순으로 돌려준다.

    `otc_only` 가 참이면 일반의약품만 본다. 자판기에서 파는 건 일반의약품뿐이고,
    후보가 25,350개에서 5,587개로 줄어 정확도가 크게 오른다.
    """
    shapes = [shape] + list(_SHAPE_NEIGHBORS.get(shape or "", ())) if shape else []
    colors = [color] + list(_COLOR_NEIGHBORS.get(color or "", ())) if color else []
    # 각인은 **토큰 단위**로 다룬다. 알약 하나에 'D-W' 와 'MPG' 처럼 떨어진 각인이
    # 있을 수 있고, 앞뒤 두 조각을 읽으면 둘이 합쳐져 넘어온다.
    # 이걸 공백만 지워 'D-WMPG' 로 조회하면 그런 각인은 세상에 없어서 0건이 된다.

    print_tokens = _tokens(print_text)

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
    if shapes:
        sql += " AND drug_shape = ANY(:shapes)"
        params["shapes"] = shapes
    if colors:
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
        for i, tok in enumerate(print_tokens[:5]):
            f_ = _CLEAN_SQL.format(col="print_front")
            b_ = _CLEAN_SQL.format(col="print_back")
            params[f"tok{i}"] = tok
            if len(tok) == 1:
                # 한 글자는 **각인 전체가 그 글자인 약**만 본다. 부분일치를 허용하면
                # 'M' 하나가 수백 개와 맞는다.
                ors.append(f"({f_} = :tok{i} OR {b_} = :tok{i})")
                continue
            ors.append(f"""(
                  {f_} LIKE :pat{i} OR {b_} LIKE :pat{i}
               OR levenshtein({f_}, :tok{i}) <= :d{i}
               OR levenshtein({b_}, :tok{i}) <= :d{i}
            )""")
            params[f"pat{i}"] = f"%{tok}%"
            # 짧은 각인에 거리 1 을 허용하면 후보가 폭발한다("AB" 는 수백 개와 거리 1).
            params[f"d{i}"] = 1 if len(tok) >= 3 else 0
        sql += " AND (" + " OR ".join(ors) + ")"

    # 후보를 넉넉히 가져와 파이썬에서 점수를 매긴다.
    # **정렬 없이 자르면 안 된다** — PostgreSQL 이 아무 순서로나 주기 때문에
    # 정답이 잘려 나갈 수 있다. 각인이 있는 것부터 가져오면 쓸모 있는 쪽이 남는다.
    sql += """ ORDER BY (print_front IS NOT NULL AND print_front <> '') DESC,
                        item_seq
               LIMIT 1200"""
    rows = session.execute(text(sql), params).mappings().all()

    out: list[tuple[float, Candidate]] = []
    for r in rows:
        score, matched = 0.0, []
        if shape and r["drug_shape"] == shape:
            score += 0.30
            matched.append("모양")
        elif shape and r["drug_shape"] in _SHAPE_NEIGHBORS.get(shape, ()):
            score += 0.12

        row_color = (r["color_class1"] or "").split(",")[0].strip()
        if color and row_color == color:
            score += 0.25
            matched.append("색")
        elif color and row_color in _COLOR_NEIGHBORS.get(color, ()):
            score += 0.10

        if print_tokens:
            front = _clean_imprint(r["print_front"])
            back = _clean_imprint(r["print_back"])
            # 토큰마다 가장 좋은 일치를 찾아, 그중 최고 점수를 준다.
            best = 0.0
            label = ""
            for tok in print_tokens:
                if tok in (front, back):
                    cur, lab = 0.45, "각인 일치"       # 통째로 같다 — 거의 확정
                elif len(tok) == 1:
                    continue                          # 한 글자는 완전일치만 인정한다
                elif front and tok in front or back and tok in back:
                    cur, lab = 0.25, "각인 부분일치"
                elif _close(tok, front) or _close(tok, back):
                    cur, lab = 0.15, "각인 비슷함"     # 한 글자 차이
                else:
                    continue
                # **긴 각인일수록 확실하다.** 'CT' 는 수십 개 약과 겹치지만
                # 'CRWSOB100' 이 맞으면 그 약 하나다. 길이에 따라 가산점을 준다.
                # ('마크' 를 지우면 각인이 짧아져 1위 정확도가 50% → 45% 로 떨어졌다)
                cur += min(0.12, max(0, len(tok) - 2) * 0.04)
                if cur > best:
                    best, label = cur, lab
            if best:
                score += best
                matched.append(label)

        if aspect and r["leng_long"] and r["leng_short"]:
            # 비율은 **거르는 조건이 아니라 점수**다(위 WHERE 참고). 거르면 각도 때문에
            # 정답이 통째로 빠지고, 점수로만 쓰면 순위만 내려간다. 다만 각인을 읽었을 때
            # 비율 조건을 뺀 만큼 후보가 늘어나므로(9.1개 → 12.6개) 가중치를 올려
            # 순위를 되돌린다. 가까울수록 더 주는 단계식이다.
            try:
                diff = abs(float(r["leng_long"]) / float(r["leng_short"]) - aspect)
                if diff <= 0.2:
                    score += 0.14
                    matched.append("비율")
                elif diff <= 0.5:
                    score += 0.07
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
        c.score = round(min(1.0, raw), 3)
    return [c for _, c in picked]
