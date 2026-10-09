"""Supabase 약품 공식 데이터(허가정보·DUR)로 RAG 컨텍스트 보강.

챗봇 본문 검색은 그대로 Pinecone(e약은요)을 쓰고, 여기서는 정확히 표로 찾아야 하는 정보만 덧붙인다.
- 의약품 허가정보(`drug_permissions`): 전문/일반 구분, 주성분. e약은요에 없는 약(대부분 전문의약품)도 이름으로 찾는다.
- DUR 품목 경고(`dur_warnings`): 임부금기·노인주의·특정연령대금기·효능군중복·분할주의
- DUR 병용금기(`dur_conflicts`): 언급된 약들의 성분 쌍이 식약처 병용금기 목록에 있는지 결정적으로 판정

설계 원칙
- `DRUG_DB_ENRICH`가 꺼져 있거나 DB 조회가 실패하면 아무것도 덧붙이지 않는다 → 기존 챗봇과 완전히 동일하게 동작.
- 모든 조회는 읽기 전용 트랜잭션(팀 공용 Supabase를 절대 수정하지 않음).
- 병용금기는 LLM 판단이 아니라 목록 일치 여부로 정한다. 목록에 없다는 것은 "안전"이 아니므로 그렇게 명시한다.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from itertools import combinations

from sqlalchemy import text

logger = logging.getLogger("uvicorn.error")

MAX_WARNINGS_PER_DRUG = 6
POPULATION_DUR_TYPES = {"pregnant": "임부금기", "elderly": "노인주의", "child": "특정연령대금기"}

# 성분명 비교 시 떼어낼 염·수화물 등 (예: "시프로플록사신염산염" == "염산시프로플록사신" == "시프로플록사신").
# 정해진 목록만 떼고 "완전히 같을 때만" 같은 성분으로 본다 — 부분 일치(예: "니코틴" ⊂ "니코틴산아미드")는
# 잘못된 병용금기 경고를 만들 수 있어서 쓰지 않는다.
_SALTS = (
    "브롬화수소산염|타르타르산염|나파디실산염|글루콘산염|프로피온산염|살리실산염|시트르산염|푸마르산염|"
    "아세트산염|말레산염|숙신산염|메실산염|베실산염|에실산염|토실산염|주석산염|구연산염|락트산염|옥살산염|"
    "파모산염|황산염|인산염|질산염|초산염|염산염|브롬화수소|고체분산체|무수물|[일이삼사]?수화물|"
    "나트륨|칼륨|칼슘|마그네슘|염산"
)
_SALT_SUFFIX = re.compile(rf"({_SALTS})$")
_SALT_PREFIX = re.compile(r"^(염산|황산|인산|질산|초산|구연산)")


@dataclass
class DrugFacts:
    item_seq: str
    item_name: str
    etc_otc: str | None = None  # 전문의약품 / 일반의약품
    ingredients: list[str] = field(default_factory=list)  # 주성분 이름
    dur_codes: set[str] = field(default_factory=set)  # DUR 성분코드(D000…) — 병용금기 정확 매칭용
    warnings: list[dict] = field(default_factory=list)  # {"type", "ingredient", "content"}
    population_note: str | None = None  # 질문 대상자 DUR 성분 확인 결과 (해당 없음 안내용)


@dataclass
class Conflict:
    drug_a: str
    ingredient_a: str
    drug_b: str
    ingredient_b: str
    reason: str


def enabled() -> bool:
    return os.getenv("DRUG_DB_ENRICH", "").strip().lower() in ("1", "true", "yes", "on")


@lru_cache(maxsize=1)
def get_engine():
    url = (os.getenv("DRUG_DATABASE_URL") or os.getenv("MAP_DATABASE_URL") or "").strip()
    if not url:
        return None
    from src.map_database import create_database_engine

    return create_database_engine(url)


def _read_only(conn) -> None:
    conn.execute(text("SET TRANSACTION READ ONLY"))
    conn.execute(text("SET LOCAL statement_timeout = '5s'"))


def normalize_ingredient(name: str) -> str:
    n = re.sub(r"\([^)]*\)", "", name or "").replace(" ", "").strip()
    n = _SALT_PREFIX.sub("", n)
    for _ in range(3):  # "…말레산염수화물"처럼 겹친 경우
        stripped = _SALT_SUFFIX.sub("", n)
        if stripped == n or len(stripped) < 2:
            break
        n = stripped
    return n


def _same_ingredient(a: str, b: str) -> bool:
    na, nb = normalize_ingredient(a), normalize_ingredient(b)
    return bool(na) and na == nb


def parse_main_ingredients(main_item_ingr: str | None) -> list[str]:
    """허가정보 주성분 문자열 "[M040353]아세트아미노펜|[M0…]…" → ["아세트아미노펜", …]"""
    if not main_item_ingr:
        return []
    names = [re.sub(r"^\[[^\]]*\]", "", part).strip() for part in main_item_ingr.split("|")]
    return [n for n in names if n]


def fetch_facts(item_seqs: list[str]) -> dict[str, DrugFacts]:
    seqs = [s for s in dict.fromkeys(item_seqs) if s]
    engine = get_engine()
    if not seqs or engine is None:
        return {}
    facts: dict[str, DrugFacts] = {}
    with engine.connect() as conn, conn.begin():
        _read_only(conn)
        for r in conn.execute(
            text("SELECT item_seq, item_name, etc_otc_name, main_item_ingr FROM public.drug_permissions "
                 "WHERE item_seq = ANY(:seqs)"),
            {"seqs": seqs},
        ).mappings():
            facts[r["item_seq"]] = DrugFacts(
                item_seq=r["item_seq"], item_name=r["item_name"], etc_otc=r["etc_otc_name"],
                ingredients=parse_main_ingredients(r["main_item_ingr"]),
            )
        for r in conn.execute(
            text("SELECT item_seq, item_name, type_name, ingr_code, ingr_name, prohbt_content, remark, effect_name "
                 "FROM public.dur_warnings WHERE item_seq = ANY(:seqs) ORDER BY item_seq, type_name"),
            {"seqs": seqs},
        ).mappings():
            f = facts.setdefault(r["item_seq"], DrugFacts(item_seq=r["item_seq"], item_name=r["item_name"]))
            if r["ingr_code"]:
                f.dur_codes.add(r["ingr_code"])
            if r["ingr_name"] and r["ingr_name"] not in f.ingredients:
                f.ingredients.append(r["ingr_name"])
            content = r["prohbt_content"] or r["remark"] or (f"{r['effect_name']} 계열 약과 중복 주의" if r["effect_name"] else "")
            entry = {"type": r["type_name"], "ingredient": r["ingr_name"], "content": content}
            if entry not in f.warnings and len(f.warnings) < MAX_WARNINGS_PER_DRUG:
                f.warnings.append(entry)
    return facts


def resolve_name_seqs(names: list[str]) -> dict[str, str]:
    """{질문 속 약 이름: 품목기준코드}. 챗봇이 이름과 '비슷한' 다른 제품(예: 원펜정→일펜정)을 고르지 않도록
    허가정보에서 정확한 제품을 먼저 찾는 데 쓴다. 꺼져 있거나 실패하면 빈 dict."""
    if not enabled():
        return {}
    try:
        return dict(_lookup_seqs(names))
    except Exception as e:
        logger.warning("drug name resolve skipped: %s: %s", type(e).__name__, str(e)[:200])
        return {}


def resolve_names(names: list[str]) -> list[DrugFacts]:
    """질문에 나온 약 이름(제품명 또는 성분명)을 허가정보에서 찾는다 — e약은요에 없는 약도 찾기 위함."""
    seqs = [seq for _, seq in _lookup_seqs(names)]
    facts = fetch_facts(seqs)
    return [facts[s] for s in seqs if s in facts]


def _lookup_seqs(names: list[str]) -> list[tuple[str, str]]:
    """우선순위: 제품명 일치 > e약은요에도 있는 품목 > 허가 '정상' > 이름이 짧은 것."""
    engine = get_engine()
    if not names or engine is None:
        return []
    pairs = []
    with engine.connect() as conn, conn.begin():
        _read_only(conn)
        for name in names:
            key = name.replace(" ", "")
            if len(key) < 2:
                continue
            rows = conn.execute(
                text("""
                    SELECT p.item_seq, p.item_name, p.cancel_name,
                           EXISTS (SELECT 1 FROM public.drugs d WHERE d.item_seq = p.item_seq) AS in_eyak
                    FROM public.drug_permissions p
                    WHERE replace(p.item_name, ' ', '') ILIKE :pat OR p.main_item_ingr ILIKE :pat
                    LIMIT 200
                """),
                {"pat": f"%{key}%"},
            ).mappings().all()
            if rows:
                best = max(rows, key=lambda r: _product_score(key, r))
                pairs.append((name, best["item_seq"]))
    return pairs


# "타이레놀" → 타이레놀정500밀리그람 처럼, 브랜드 뒤에 제형·함량만 붙은 기본 제품을 고르기 위한 규칙.
# (가장 짧은 이름을 고르면 "타이레놀콜드-에스정" 같은 다른 약, 유사도로 고르면 "8시간이알서방정"이 뽑히던 문제)
_PLAIN_REMAINDER = re.compile(
    r"^(정|필름코팅정|츄어블정|캡슐|연질캡슐|시럽|현탁액|액|산|과립)?"
    r"[\d.,/]*(밀리그람|밀리그램|mg|그램|g|mL|밀리리터)?$",
    re.IGNORECASE,
)


def _product_score(key: str, row) -> tuple:
    name = re.sub(r"\([^)]*\)", "", row["item_name"]).replace(" ", "")
    starts = name.startswith(key)
    plain = starts and bool(_PLAIN_REMAINDER.match(name[len(key):]))
    tablet = plain and name[len(key):].startswith("정")
    return (
        key in name,  # 제품명에 들어 있음 (성분명으로만 걸린 것보다 우선)
        plain,  # 브랜드 + 제형/함량만
        tablet,  # 같은 조건이면 정제 우선
        starts,
        bool(row["in_eyak"]),  # e약은요에도 있는 품목
        row["cancel_name"] == "정상",
        -len(name),
    )


@lru_cache(maxsize=1)
def _all_conflicts() -> tuple:
    engine = get_engine()
    with engine.connect() as conn, conn.begin():
        _read_only(conn)
        rows = conn.execute(
            text("SELECT ingr_code_a, ingr_code_b, ingr_name_a, ingr_name_b, prohbt_content FROM public.dur_conflicts")
        ).all()
    return tuple(tuple(r) for r in rows)  # 356행 정도라 메모리에 한 번만 올려둔다


def find_conflicts(drugs: list[DrugFacts], conflict_rows=None) -> list[Conflict]:
    """서로 다른 약 쌍마다 성분 조합이 DUR 병용금기 목록에 있는지 확인한다 (성분코드 우선, 없으면 성분명)."""
    rows = conflict_rows if conflict_rows is not None else _all_conflicts()
    found: list[Conflict] = []
    for a, b in combinations(drugs, 2):
        if a.item_seq == b.item_seq:
            continue
        for code_a, code_b, name_a, name_b, reason in rows:
            for x, y in ((a, b), (b, a)):
                hit_x = code_a in x.dur_codes or any(_same_ingredient(i, name_a) for i in x.ingredients)
                hit_y = code_b in y.dur_codes or any(_same_ingredient(i, name_b) for i in y.ingredients)
                if hit_x and hit_y:
                    c = Conflict(x.item_name, name_a, y.item_name, name_b, reason or "병용금기")
                    if c not in found:
                        found.append(c)
                    break
    return found


@lru_cache(maxsize=4)
def _population_ingredients(type_name: str) -> dict:
    """DUR 경고 유형(예: 임부금기)에 해당하는 성분 → (성분명, 내용). 성분 단위로 금기 여부를 확인하기 위함."""
    engine = get_engine()
    with engine.connect() as conn, conn.begin():
        _read_only(conn)
        rows = conn.execute(
            text("SELECT DISTINCT ON (ingr_name) ingr_name, prohbt_content FROM public.dur_warnings "
                 "WHERE type_name = :t AND ingr_name IS NOT NULL ORDER BY ingr_name, prohbt_content NULLS LAST"),
            {"t": type_name},
        ).all()
    return {normalize_ingredient(name): (name, content) for name, content in rows}


def check_population_by_ingredient(drugs: list[DrugFacts], population: str | None, table=None) -> None:
    """질문 대상자(임산부/노인/소아)에 대해 각 약의 '주성분'이 DUR 목록에 있는지 확인해 결과를 채운다."""
    focus = POPULATION_DUR_TYPES.get(population or "")
    if not focus:
        return
    table = table if table is not None else _population_ingredients(focus)
    for d in drugs:
        if any(w["type"] == focus for w in d.warnings):
            continue  # 품목 자체에 이미 경고가 있음
        hits = [table[normalize_ingredient(i)] for i in d.ingredients if normalize_ingredient(i) in table]
        if hits:
            name, content = hits[0]
            d.warnings.insert(0, {"type": focus, "ingredient": name, "content": f"{content or ''} (같은 성분 기준)".strip()})
        elif d.ingredients:
            d.population_note = (f"주성분({', '.join(d.ingredients[:3])})은 식약처 DUR {focus} 목록에 없음 "
                                 "— 목록에 없다고 안전하다는 뜻은 아니며, e약은요 주의사항을 함께 확인할 것")


def format_block(drugs: list[DrugFacts], conflicts: list[Conflict], population: str | None, check_pairs: bool) -> str:
    """LLM 컨텍스트에 덧붙일 공식 데이터 블록."""
    if not drugs:
        return ""
    focus = POPULATION_DUR_TYPES.get(population or "")
    lines = ["[식약처 공식 데이터: 의약품 허가정보 · DUR(의약품 안전사용 서비스)]"]
    for d in drugs:
        head = f"- {d.item_name}"
        if d.etc_otc:
            head += f" · {d.etc_otc}"
        if d.ingredients:
            head += f" · 주성분: {', '.join(d.ingredients[:4])}"
        lines.append(head)
        for w in sorted(d.warnings, key=lambda w: w["type"] != focus):  # 질문 대상자 관련 경고를 먼저
            mark = " ★질문 대상자 관련" if focus and w["type"] == focus else ""
            detail = f": {w['content']}" if w["content"] else ""
            lines.append(f"  · DUR {w['type']}{mark} ({w['ingredient']}){detail}")
        if d.population_note:
            lines.append(f"  · {d.population_note}")
        if not d.warnings:
            lines.append("  · DUR 품목 경고: 등록된 항목 없음")
    if check_pairs and len(drugs) >= 2:
        if conflicts:
            lines.append("[DUR 병용금기 판정] 아래 조합은 식약처 DUR 병용금기에 해당합니다:")
            lines.extend(f"- {c.drug_a}({c.ingredient_a}) + {c.drug_b}({c.ingredient_b}): {c.reason}" for c in conflicts)
        else:
            lines.append("[DUR 병용금기 판정] 이 약들의 성분 조합은 식약처 DUR 병용금기 목록에서 확인되지 않았습니다. "
                         "(목록에 없다는 것이 함께 먹어도 안전하다는 뜻은 아닙니다)")
    return "\n".join(lines)


PERMISSION_NAMESPACE = "permissions"  # scripts/index_permissions.py가 채운 Pinecone namespace
PERMISSION_SEARCH_K = 4


def permission_search_enabled() -> bool:
    """허가정보 의미 검색 — DUR 보강이 켜져 있으면 기본으로 켜지고, DRUG_PERMISSION_SEARCH=0으로만 끈다."""
    return enabled() and os.getenv("DRUG_PERMISSION_SEARCH", "1").strip() not in ("0", "false", "off", "no")


def search_permissions(vector_store, query: str) -> list:
    """e약은요에 없는 약(주로 전문의약품)을 약효분류·성분으로 의미 검색. 실패하면 []."""
    if not permission_search_enabled():
        return []
    try:
        return vector_store.similarity_search(query, k=PERMISSION_SEARCH_K, namespace=PERMISSION_NAMESPACE)
    except Exception as e:
        logger.warning("permission search skipped: %s: %s", type(e).__name__, str(e)[:200])
        return []


def format_permission_block(docs) -> str:
    if not docs:
        return ""
    lines = ["[식약처 의약품 허가정보 검색 결과 (e약은요에 없는 약, 주로 전문의약품 · 효능 설명문 없음)]"]
    lines += [f"- {d.page_content.replace(chr(10), ' · ')}" for d in docs]
    return "\n".join(lines)


PERMISSION_PROMPT_RULES = """
[식약처 의약품 허가정보 검색 결과] 블록 규칙:
- 질문한 약이나 분류가 위 e약은요 자료에 있으면 e약은요 자료를 우선하고, 이 블록은 e약은요에 없는 약을 물었을 때만 사용하세요.
- 이 블록에는 구분(전문/일반)·약효분류·주성분·성상·보관법만 있고 효능·용법·부작용 설명은 없습니다. 없는 내용을 지어내지 마세요.
- 전문의약품은 의사의 처방이 필요하다고 반드시 안내하고, 복용법이나 복용 여부를 판단해 주지 마세요. 스스로 골라 먹을 약으로 추천하지 마세요."""


DUR_PROMPT_RULES = """
[식약처 공식 데이터] 블록이 있으면 다음 규칙도 지키세요.
- 이 블록은 식약처 의약품 허가정보와 DUR(의약품 안전사용 서비스) 공식 데이터입니다. 위 자료와 함께 근거로 사용하세요.
- [DUR 병용금기 판정]에 해당 조합이 있으면 "함께 복용하면 안 되는 조합입니다(식약처 DUR 병용금기)"라고 분명히 알리고 사유를 쉬운 말로 전하세요.
- 병용금기 목록에 없다고 해서 "함께 먹어도 안전하다"고 결론 내리지 마세요.
- "★질문 대상자 관련" 표시가 있는 DUR 경고(임부금기·노인주의·특정연령대금기)는 반드시 답변에 포함하고, 추천 질문이면 그 약은 추천에서 제외하세요.
- 전문의약품이면 의사의 처방이 필요한 약이라고 안내하고 복용 방법을 임의로 안내하지 마세요.
- 이 블록의 정보를 사용했다면 답변 본문에 "식약처 DUR" 또는 "식약처 허가정보"를 근거로 언급하세요."""


def build_enrichment(
    item_seqs: list[str],
    mentioned_names: list[str] | None = None,
    population: str | None = None,
    check_pairs: bool = False,
) -> tuple[str, list[DrugFacts]]:
    """(컨텍스트 블록, 조회된 약 목록). 꺼져 있거나 실패하면 ("", [])."""
    if not enabled():
        return "", []
    try:
        facts = fetch_facts(item_seqs)
        drugs = list(facts.values())
        if mentioned_names:  # e약은요에 없는 약도 이름으로 찾아 병용금기 판정에 포함
            for d in resolve_names(mentioned_names):
                if all(d.item_seq != x.item_seq for x in drugs):
                    drugs.append(d)
        check_population_by_ingredient(drugs, population)
        conflicts = find_conflicts(drugs) if check_pairs and len(drugs) >= 2 else []
        return format_block(drugs, conflicts, population, check_pairs), drugs
    except Exception as e:  # 공식 데이터 보강은 부가 기능 — 실패해도 기존 답변 흐름은 그대로
        logger.warning("drug facts enrichment skipped: %s: %s", type(e).__name__, str(e)[:200])
        return "", []
