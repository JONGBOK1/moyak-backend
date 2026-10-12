"""상담 음성·요약 흐름 스키마 (한별 구현 이식, 2026-10-12)."""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

ClientID = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9_-]{1,80}$")]
SummaryItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]

# 요약 6개 항목 (약사 검토·수정 화면과 사용자 공개 문구에서 같은 순서/라벨 사용)
SUMMARY_LABELS = {
    "symptoms": "호소한 증상",
    "discussion": "상담 내용",
    "medication_guidance": "복약 안내",
    "precautions": "주의사항",
    "follow_up": "후속 안내",
    "needs_verification": "확인이 필요한 내용",
}


class SummaryContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symptoms: list[SummaryItem] = Field(max_length=30)
    discussion: list[SummaryItem] = Field(max_length=30)
    medication_guidance: list[SummaryItem] = Field(max_length=30)
    precautions: list[SummaryItem] = Field(max_length=30)
    follow_up: list[SummaryItem] = Field(max_length=30)
    needs_verification: list[SummaryItem] = Field(max_length=30)
