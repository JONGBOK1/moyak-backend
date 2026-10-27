"""개인 맞춤 복약 안전 판정.

"이 약, 제가 먹어도 되나요?" 에 답하는 부분이다.
지금까지 모은 데이터가 여기서 처음으로 같이 쓰인다.

    user_medications (내가 먹는 약)
        → drug_ingredients (그 약의 성분)
        → dur_conflicts (성분쌍 금기)      ← 병용금기
    user_conditions (알러지·임신·지병)
        → drug_ingredients / dur_warnings  ← 알러지·임부금기
    users.birth_year
        → dur_warnings (노인주의·연령대금기)

**설계 원칙: 모르는 것을 "안전하다"고 말하지 않는다.**

DUR 성분코드가 붙어 있는 약은 자판기 대상 4,748개 중 588개(12.4%)뿐이다.
나머지는 병용금기를 **판정할 수 없는** 것이지 안전한 게 아니다.
의약품을 다루므로 이 둘을 반드시 구분해서 돌려준다
(`can_check_interactions` 플래그).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

#: 노인주의 판정 기준 나이 (DUR 고시 기준)
ELDERLY_AGE = 65

#: 생년만 알고 생일은 모르니 만 나이를 정확히 낼 수 없다. 그래서 화면 표시와 DUR 판정
#: **둘 다** 연 나이(올해 - 생년)를 쓴다. 예전엔 판정만 세는 나이(+1)를 써서, 화면에
#: 64세로 뜨는 환자에게 노인주의 경고가 붙어 약사가 왜 뜨는지 알 수 없었다.
#: 연 나이는 만 나이보다 최대 1살 많으므로 노인주의·연령금기가 경계에서 1년 일찍
#: 걸린다 — 경고를 놓치는 쪽보다 낫다고 보고 그대로 둔다.
def year_age(birth_year: int | None) -> int | None:
    """생년으로 계산한 연 나이. 생년이 없으면 None."""
    return (date.today().year - birth_year) if birth_year else None



class Level(str, Enum):
    """경고 심각도. 화면 색상·정렬에 그대로 쓴다."""

    DANGER = "danger"     # 같이 먹으면 안 됨 / 금기
    WARNING = "warning"   # 주의해서 복용
    INFO = "info"         # 참고
    UNKNOWN = "unknown"   # 판정 불가 — "안전"이 아니다


def _meaningful(value: str | None) -> str | None:
    """내용이 실제로 있는 문자열만 통과시킨다.

    DUR 원본에는 사유가 `-` 나 `.` 한 글자로만 들어있는 행이 있다.
    그대로 화면에 뿌리면 "임부금기 — -" 처럼 보인다.
    """
    if not value:
        return None
    stripped = value.strip(" -.·,:;\n\t")
    return value.strip() if stripped else None


@dataclass
class Finding:
    level: Level
    category: str          # 병용금기 / 알러지 / 임부금기 / 노인주의 ...
    message: str
    detail: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "category": self.category,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class SafetyReport:
    item_seq: str
    item_name: str
    findings: list[Finding] = field(default_factory=list)
    #: 이 약에 DUR 성분코드가 있어 병용금기를 실제로 따져볼 수 있었는가
    can_check_interactions: bool = False
    #: 사용자가 등록한 복용약 중 성분코드가 있어 대조 가능했던 것
    checked_medications: list[str] = field(default_factory=list)
    unknown_medications: list[str] = field(default_factory=list)
    #: 위 '모르는 약' 을 왜 모르는지 둘로 나눈 것. 뭉뚱그리면 약사가 할 일이 안 보인다.
    #:   unidentified — 약을 특정하지 못했다("동네약국에서 받은 소화제"). 약사가 이름을 물어보면 풀린다.
    #:   no_dur       — 성분은 아는데 병용금기로 **지정된 성분이 없다**. 충돌할 수가 없으므로 사실상 깨끗하다.
    #: 전자만 '확인 불가' 로 센다. 후자까지 회색으로 띄우면 경고가 너무 자주 떠 약사가 안 읽게 된다.
    unidentified_medications: list[str] = field(default_factory=list)
    no_dur_medications: list[str] = field(default_factory=list)
    #: 처방하려는 약 자체의 주성분을 허가정보에서 읽을 수 있었는가
    drug_ingredients_known: bool = False

    @property
    def unresolved_medications(self) -> list[str]:
        """아직 정체를 모르는 복용약. 분류가 안 됐으면 모르는 것으로 친다.

        `unidentified`/`no_dur` 는 check_drug_for_user 가 채운다. 그 분류 없이 보고서를
        직접 만들면(테스트·다른 호출부) 판단 근거가 없는 셈이므로 **안전한 쪽으로** 기운다.
        여기서 info 로 떨어뜨리면 확인 안 된 약이 초록불에 묻힌다.
        """
        if self.unidentified_medications:
            return self.unidentified_medications
        safe = set(self.no_dur_medications)
        return [m for m in self.unknown_medications if m not in safe]

    @property
    def level(self) -> Level:
        for lv in (Level.DANGER, Level.WARNING):
            if any(f.level is lv for f in self.findings):
                return lv
        # 확인 못 한 게 하나라도 있으면 "확인 불가" — 초록(info)으로 보이면 약사가 안전으로 오해한다.
        # 단 **정말 모르는 것만** 센다. 성분을 아는데 금기 지정 성분이 없는 약은 충돌할 수가
        # 없으므로 모르는 게 아니다. 그것까지 회색으로 띄우면 회색이 너무 흔해져 무시당한다.
        cannot_check_this_drug = not self.can_check_interactions and not self.drug_ingredients_known
        if cannot_check_this_drug or self.unresolved_medications or any(
                f.level is Level.UNKNOWN for f in self.findings):
            return Level.UNKNOWN
        return Level.INFO

    @property
    def summary(self) -> str:
        danger = sum(f.level is Level.DANGER for f in self.findings)
        warn = sum(f.level is Level.WARNING for f in self.findings)
        if danger:
            return f"함께 복용하면 안 되는 사유가 {danger}건 있습니다"
        if warn:
            return f"주의가 필요한 사항이 {warn}건 있습니다"
        if not self.can_check_interactions and not self.drug_ingredients_known:
            return "이 약은 성분 정보가 없어 병용금기를 확인할 수 없습니다"
        if self.unresolved_medications:
            unresolved = self.unresolved_medications
            names = ", ".join(f"'{n}'" for n in unresolved[:2])
            more = f" 외 {len(unresolved) - 2}건" if len(unresolved) > 2 else ""
            return f"복용약이 어떤 약인지 확인하지 못했습니다: {names}{more}. 환자분께 약 이름을 여쭤보세요"
        if any(f.level is Level.UNKNOWN for f in self.findings):
            return "일부 항목을 확인하지 못했습니다"
        if not self.can_check_interactions:
            return "이 약은 병용금기로 지정된 성분이 없습니다"
        return "등록하신 정보와 충돌하는 항목은 찾지 못했습니다"

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_seq": self.item_seq,
            "item_name": self.item_name,
            "level": self.level.value,
            "summary": self.summary,
            "can_check_interactions": self.can_check_interactions,
            "checked_medications": self.checked_medications,
            "unknown_medications": self.unknown_medications,
            "unidentified_medications": self.unidentified_medications,
            "no_dur_medications": self.no_dur_medications,
            "drug_ingredients_known": self.drug_ingredients_known,
            "findings": [f.to_dict() for f in self.findings],
        }


# ── 개별 판정 ──────────────────────────────────────────────────────
def _check_interactions(session: Session, user_id: str, item_seq: str, report: SafetyReport) -> None:
    """병용금기: 내가 먹는 약의 성분 × 이 약의 성분 → dur_conflicts."""
    rows = session.execute(text("""
        WITH target AS (
            SELECT ingr_code, ingr_name FROM drug_ingredients WHERE item_seq = :item_seq
        ),
        mine AS (
            SELECT DISTINCT di.ingr_code, di.ingr_name, um.drug_name
            FROM user_medications um
            JOIN drug_ingredients di ON di.item_seq = um.item_seq
            WHERE um.user_id = CAST(:user_id AS uuid)
              AND um.ended_on IS NULL
        )
        SELECT m.drug_name, m.ingr_name AS my_ingr, t.ingr_name AS target_ingr,
               c.prohbt_content
        FROM mine m
        CROSS JOIN target t
        JOIN dur_conflicts c
          ON c.ingr_code_a = LEAST(m.ingr_code, t.ingr_code)
         AND c.ingr_code_b = GREATEST(m.ingr_code, t.ingr_code)
        WHERE m.ingr_code <> t.ingr_code
    """), {"item_seq": item_seq, "user_id": user_id}).mappings().all()

    for r in rows:
        report.findings.append(Finding(
            level=Level.DANGER,
            category="병용금기",
            message=f"복용 중인 '{r['drug_name']}' 과 함께 드시면 안 됩니다",
            detail=f"{r['my_ingr']} × {r['target_ingr']}"
                   + (f" — {r['prohbt_content']}" if r["prohbt_content"] else ""),
        ))


def _check_allergies(session: Session, user_id: str, item_seq: str, report: SafetyReport) -> None:
    """알러지: 등록한 알러지 성분코드가 이 약에 들어있는가."""
    rows = session.execute(text("""
        SELECT uc.label, di.ingr_name
        FROM user_conditions uc
        JOIN drug_ingredients di ON di.ingr_code = uc.ingr_code
        WHERE uc.user_id = CAST(:user_id AS uuid)
          AND uc.condition_type = 'allergy'
          AND uc.ingr_code IS NOT NULL
          AND di.item_seq = :item_seq
    """), {"item_seq": item_seq, "user_id": user_id}).mappings().all()

    for r in rows:
        report.findings.append(Finding(
            level=Level.DANGER,
            category="알러지",
            message=f"등록하신 알러지 성분('{r['label']}')이 들어 있습니다",
            detail=r["ingr_name"],
        ))
    _check_unmatched_allergies(session, user_id, item_seq, report, {r["label"] for r in rows})


def _check_unmatched_allergies(session: Session, user_id: str, item_seq: str, report: SafetyReport,
                               already: set[str]) -> None:
    """성분코드 대조로 못 잡은 알러지를 허가정보 **주성분 전체 목록**으로 다시 본다 (2026-10-27).

    `drug_ingredients` 는 DUR 성분만 담는 색인이라, 거기 없는 성분에 대한 알러지는 위 쿼리로
    절대 안 걸린다. 알러지를 글자로만 적은 경우("페니실린", 성분코드 없음)도 마찬가지다.
    "대조를 못 했다" 를 "없다" 로 넘기면 초록불이 뜨므로, 끝까지 확인 못 한 알러지는
    확인 불가(unknown)로 남겨 약사가 직접 묻게 한다.
    """
    from src.rag.drug_facts import normalize_ingredient, parse_main_ingredients

    allergies = session.execute(text("""
        SELECT uc.label, uc.ingr_code,
               (SELECT di.ingr_name FROM drug_ingredients di WHERE di.ingr_code = uc.ingr_code LIMIT 1) AS ingr_name
        FROM user_conditions uc
        WHERE uc.user_id = CAST(:user_id AS uuid) AND uc.condition_type = 'allergy'
    """), {"user_id": user_id}).mappings().all()
    allergies = [a for a in allergies if a["label"] not in already]
    if not allergies:
        return

    main = session.execute(text(
        "SELECT main_item_ingr FROM drug_permissions WHERE item_seq = :s LIMIT 1"), {"s": item_seq}).scalar()
    ingredients = {normalize_ingredient(n) for n in parse_main_ingredients(main)} - {""}

    for a in allergies:
        name = normalize_ingredient(a["ingr_name"] or a["label"] or "")
        if ingredients and name and name in ingredients:
            report.findings.append(Finding(
                level=Level.DANGER,
                category="알러지",
                message=f"등록하신 알러지 성분('{a['label']}')이 들어 있습니다",
                detail=a["ingr_name"] or a["label"],
            ))
        elif ingredients and a["ingr_name"]:
            continue  # 성분이 정확히 특정된 알러지이고, 주성분 전체 목록에 없다 → 확인됨
        else:
            # 주성분을 모르거나, 알러지가 글자로만 적혀 있어("페니실린계") 정확히 대조할 수 없다
            report.findings.append(Finding(
                level=Level.UNKNOWN,
                category="알러지 확인 필요",
                message=f"등록하신 알러지('{a['label']}')는 자동으로 확인하지 못했습니다",
                detail="안전하다는 뜻이 아닙니다. 환자분께 알러지 성분을 직접 확인해 주세요.",
            ))


def _check_dur_warnings(session: Session, user_id: str, item_seq: str, report: SafetyReport) -> None:
    """임부금기·노인주의·특정연령대금기 — 사용자 상태와 대조."""
    profile = session.execute(text("""
        SELECT u.birth_year,
               EXISTS (SELECT 1 FROM user_conditions
                       WHERE user_id = u.id AND condition_type = 'pregnancy') AS pregnant,
               EXISTS (SELECT 1 FROM user_conditions
                       WHERE user_id = u.id AND condition_type = 'breastfeeding') AS breastfeeding
        FROM users u WHERE u.id = CAST(:user_id AS uuid)
    """), {"user_id": user_id}).mappings().first()
    if not profile:
        return

    age = year_age(profile["birth_year"])

    #: 사용자 상태 → 신경 써야 할 DUR 경고 종류
    applicable: list[str] = []
    if profile["pregnant"] or profile["breastfeeding"]:
        applicable.append("임부금기")
    if age is not None and age >= ELDERLY_AGE:
        applicable.append("노인주의")
    if age is not None and age < 19:
        applicable.append("특정연령대금기")
    if not applicable:
        return

    rows = session.execute(text("""
        SELECT DISTINCT type_name, prohbt_content, effect_name
        FROM dur_warnings
        WHERE item_seq = :item_seq AND type_name = ANY(:kinds)
    """), {"item_seq": item_seq, "kinds": applicable}).mappings().all()

    for r in rows:
        # 원본에 사유가 "-" 한 글자로만 들어있는 행이 있다. 내용 없는 것으로 본다.
        reason = _meaningful(r["prohbt_content"]) or _meaningful(r["effect_name"])
        report.findings.append(Finding(
            level=Level.DANGER if "금기" in r["type_name"] else Level.WARNING,
            category=r["type_name"],
            message=f"{r['type_name']}에 해당합니다",
            detail=reason or "구체적 사유는 제공되지 않았습니다. 약사와 상의하세요.",
        ))


# ── 진입점 ────────────────────────────────────────────────────────
def check_drug_for_user(session: Session, user_id: str, item_seq: str) -> SafetyReport:
    """사용자 1명 × 약 1개의 안전성 판정."""
    drug = session.execute(
        text("SELECT item_seq, item_name FROM drugs WHERE item_seq = :s"),
        {"s": item_seq},
    ).mappings().first()
    if not drug:
        raise ValueError(f"등록되지 않은 약입니다: {item_seq}")

    report = SafetyReport(item_seq=drug["item_seq"], item_name=drug["item_name"])

    # 이 약에 병용금기 지정 성분이 있는가?
    report.can_check_interactions = bool(session.execute(
        text("SELECT 1 FROM drug_ingredients WHERE item_seq = :s LIMIT 1"),
        {"s": item_seq},
    ).first())
    #: 없다면 두 경우다 — 주성분을 아는데 금기 지정 성분이 아니거나(충돌 불가),
    #: 주성분 자체를 모르거나(판정 불가). 허가정보 주성분란이 있는지로 가른다.
    report.drug_ingredients_known = bool(session.execute(
        text("""SELECT 1 FROM drug_permissions
                 WHERE item_seq = :s AND main_item_ingr IS NOT NULL AND main_item_ingr <> '' LIMIT 1"""),
        {"s": item_seq},
    ).first())

    meds = session.execute(text("""
        SELECT um.drug_name,
               EXISTS (SELECT 1 FROM drug_ingredients di WHERE di.item_seq = um.item_seq) AS known,
               EXISTS (SELECT 1 FROM drug_permissions p
                        WHERE p.item_seq = um.item_seq
                          AND p.main_item_ingr IS NOT NULL AND p.main_item_ingr <> '') AS ingr_known
        FROM user_medications um
        WHERE um.user_id = CAST(:user_id AS uuid) AND um.ended_on IS NULL
    """), {"user_id": user_id}).mappings().all()
    report.checked_medications = [m["drug_name"] for m in meds if m["known"]]
    report.unknown_medications = [m["drug_name"] for m in meds if not m["known"]]
    #: 대조하지 못한 약을 "약을 특정 못 함" 과 "금기 지정 성분이 없음" 으로 가른다.
    report.unidentified_medications = [
        m["drug_name"] for m in meds if not m["known"] and not m["ingr_known"]]
    report.no_dur_medications = [
        m["drug_name"] for m in meds if not m["known"] and m["ingr_known"]]

    if report.can_check_interactions:
        _check_interactions(session, user_id, item_seq, report)
    _check_allergies(session, user_id, item_seq, report)
    _check_dur_warnings(session, user_id, item_seq, report)

    # 판정하지 못한 부분이 있으면 그 사실 자체를 결과에 남긴다.
    # **"못 했다" 와 "할 게 없었다" 를 구분한다.** 둘 다 '확인 불가' 로 띄우면 회색이 너무
    # 흔해지고, 약사가 회색을 읽지 않게 되면 진짜 확인이 필요한 건까지 묻힌다.
    if not report.can_check_interactions:
        if report.drug_ingredients_known:
            #: 주성분은 아는데 그중 병용금기로 지정된 성분이 없다 → 짝이 될 성분이 없으니 충돌 불가
            report.findings.append(Finding(
                level=Level.INFO,
                category="병용금기 없음",
                message="이 약에는 병용금기로 지정된 성분이 없습니다",
                detail="식약처가 병용금기로 지정한 성분을 포함하지 않아, 함께 먹으면 안 되는 약이 없습니다.",
            ))
        else:
            report.findings.append(Finding(
                level=Level.UNKNOWN,
                category="확인 불가",
                message="이 약은 성분 정보가 없어 병용금기를 확인하지 못했습니다",
                detail="안전하다는 뜻이 아닙니다. 약사와 상의하세요.",
            ))

    if report.unresolved_medications:
        n = len(report.unresolved_medications)
        report.findings.append(Finding(
            level=Level.UNKNOWN,
            category="확인 불가",
            message=f"복용 중인 약 {n}개가 어떤 약인지 확인되지 않습니다",
            detail="환자분께 약 이름을 여쭤보세요: " + ", ".join(report.unresolved_medications),
        ))
    elif report.no_dur_medications:
        #: 이쪽은 약사가 더 할 일이 없다. 사실만 알리고 경고로 세지 않는다.
        report.findings.append(Finding(
            level=Level.INFO,
            category="병용금기 없음",
            message=f"복용 중인 약 {len(report.no_dur_medications)}개는 병용금기 지정 성분이 없습니다",
            detail=", ".join(report.no_dur_medications),
        ))

    return report


def find_nearby_machines(
    session: Session, lon: float, lat: float, *, radius_m: int = 2000, limit: int = 5
) -> list[dict[str, Any]]:
    """내 위치에서 가까운 자판기. PostGIS `geography` 라 미터 단위로 바로 계산된다."""
    rows = session.execute(text("""
        SELECT code, name, address, operating_hours,
               round(ST_Distance(location, ST_MakePoint(:lon, :lat)::geography)::numeric) AS distance_m
        FROM vending_machines
        WHERE is_active
          AND ST_DWithin(location, ST_MakePoint(:lon, :lat)::geography, :radius)
        ORDER BY location <-> ST_MakePoint(:lon, :lat)::geography
        LIMIT :limit
    """), {"lon": lon, "lat": lat, "radius": radius_m, "limit": limit}).mappings().all()
    return [dict(r) for r in rows]
