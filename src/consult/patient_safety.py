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

    @property
    def level(self) -> Level:
        for lv in (Level.DANGER, Level.WARNING, Level.INFO):
            if any(f.level is lv for f in self.findings):
                return lv
        return Level.UNKNOWN if not self.can_check_interactions else Level.INFO

    @property
    def summary(self) -> str:
        danger = sum(f.level is Level.DANGER for f in self.findings)
        warn = sum(f.level is Level.WARNING for f in self.findings)
        if danger:
            return f"함께 복용하면 안 되는 사유가 {danger}건 있습니다"
        if warn:
            return f"주의가 필요한 사항이 {warn}건 있습니다"
        if not self.can_check_interactions:
            return "이 약은 성분 정보가 없어 병용금기를 확인할 수 없습니다"
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

    age = date.today().year - profile["birth_year"] + 1 if profile["birth_year"] else None

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

    # 이 약의 성분을 아는가? 모르면 병용금기는 "판정 불가"다.
    report.can_check_interactions = bool(session.execute(
        text("SELECT 1 FROM drug_ingredients WHERE item_seq = :s LIMIT 1"),
        {"s": item_seq},
    ).first())

    meds = session.execute(text("""
        SELECT um.drug_name,
               EXISTS (SELECT 1 FROM drug_ingredients di WHERE di.item_seq = um.item_seq) AS known
        FROM user_medications um
        WHERE um.user_id = CAST(:user_id AS uuid) AND um.ended_on IS NULL
    """), {"user_id": user_id}).mappings().all()
    report.checked_medications = [m["drug_name"] for m in meds if m["known"]]
    report.unknown_medications = [m["drug_name"] for m in meds if not m["known"]]

    if report.can_check_interactions:
        _check_interactions(session, user_id, item_seq, report)
    _check_allergies(session, user_id, item_seq, report)
    _check_dur_warnings(session, user_id, item_seq, report)

    # 판정하지 못한 부분이 있으면 그 사실 자체를 결과에 남긴다
    if not report.can_check_interactions:
        report.findings.append(Finding(
            level=Level.UNKNOWN,
            category="확인 불가",
            message="이 약은 성분 정보가 없어 병용금기를 확인하지 못했습니다",
            detail="안전하다는 뜻이 아닙니다. 약사와 상의하세요.",
        ))
    elif report.unknown_medications:
        report.findings.append(Finding(
            level=Level.UNKNOWN,
            category="확인 불가",
            message=f"복용 중인 약 {len(report.unknown_medications)}개는 성분 정보가 없어 대조하지 못했습니다",
            detail=", ".join(report.unknown_medications),
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
