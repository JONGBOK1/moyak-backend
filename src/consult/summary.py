"""화상 상담 종료 시 대화 요약 생성.

요약 재료는 (1) 상담 전 챗봇 대화(chat_summary)와 (2) 상담 중 사용자-약사 텍스트 채팅이다.
화상 통화의 음성 내용은 서버가 받지 않으므로 요약에 포함되지 않는다.

요약은 기록/복기용이다 — 대화에 없는 의학 정보를 새로 만들어 넣지 않도록 프롬프트로 강제한다
(프로젝트 핵심 원칙: 근거 없는 약 정보 생성 금지).
"""

from langchain_core.messages import HumanMessage, SystemMessage

from src.consult.models import ConsultationRequest, MessageSender

SUMMARY_SYSTEM_PROMPT = """당신은 약국 화상 상담 기록을 정리하는 도우미입니다.
아래 [상담 전 챗봇 대화]와 [상담 중 채팅]만 보고, 사용자(주로 어르신)가 나중에 다시 읽기 쉽게 상담 내용을 요약하세요.

규칙:
- 대화에 실제로 나온 내용만 쓰세요. 대화에 없는 약 이름, 복용량, 효능, 주의사항을 절대 새로 만들어 넣지 마세요.
- 약사가 안내한 복용법/주의사항은 약사가 말한 그대로 옮기세요.
- 짧고 쉬운 문장으로, 아래 형식을 지키세요. 해당 내용이 대화에 없으면 그 항목은 "대화에서 언급되지 않았습니다"라고 쓰세요.

■ 상담 내용: (사용자가 말한 증상/궁금한 점)
■ 약사 안내: (약사가 안내한 내용, 권한 약)
■ 처방 결과: (아래 [처방 결과] 그대로)
■ 기억할 점: (복용법/주의사항 중 약사가 강조한 것)

마지막 줄에 "※ 음성 대화 내용은 요약에 포함되지 않았습니다. 궁금한 점은 약사에게 다시 문의하세요."를 그대로 붙이세요."""

EMPTY_SUMMARY = (
    "상담 중 주고받은 채팅이나 사전 챗봇 대화가 없어 요약할 내용이 없습니다.\n"
    "※ 음성 대화 내용은 요약에 포함되지 않습니다. 궁금한 점은 약사에게 다시 문의하세요."
)


def _decision_text(consultation: ConsultationRequest) -> str:
    if consultation.status == "approved" and consultation.purchase is not None:
        return f"약사 승인 — 처방약: {consultation.purchase.drug_item_name}"
    if consultation.status == "rejected":
        return f"약사 거절 — 사유: {consultation.decision_reason or '별도 사유 없음'}"
    return "아직 약사의 승인/거절 결정이 없습니다."


def build_transcript(consultation: ConsultationRequest, direct_request_summary: str) -> str | None:
    """요약할 재료를 한 덩어리 텍스트로 만든다. 요약할 대화가 전혀 없으면 None."""
    chat_before = consultation.chat_summary if consultation.chat_summary != direct_request_summary else ""
    lines = [
        f"{'약사' if m.sender_role == MessageSender.PHARMACIST else '사용자'}: {m.content}"
        for m in consultation.messages
    ]
    if not chat_before and not lines:
        return None
    return (
        f"[상담 전 챗봇 대화]\n{chat_before or '(없음)'}\n\n"
        f"[상담 중 채팅]\n{chr(10).join(lines) or '(없음)'}\n\n"
        f"[처방 결과]\n{_decision_text(consultation)}"
    )


def summarize(llm, consultation: ConsultationRequest, direct_request_summary: str) -> str:
    transcript = build_transcript(consultation, direct_request_summary)
    if transcript is None:
        return EMPTY_SUMMARY  # 요약할 대화가 없으면 LLM을 부르지 않는다 (비용/환각 방지)
    response = llm.invoke([SystemMessage(content=SUMMARY_SYSTEM_PROMPT), HumanMessage(content=transcript)])
    return response.content.strip()
