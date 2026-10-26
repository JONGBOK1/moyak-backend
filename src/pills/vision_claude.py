"""`vision_llm` 의 Claude 쪽. 같은 질문·같은 JSON 스키마를 Claude 에게 보낸다.

OpenAI 와 따로 둔 건 SDK 가 달라서다. 질문(`_PROMPT`)과 스키마(`_SCHEMA`)는
`vision_llm` 것을 그대로 받으므로 **둘을 같은 조건으로 비교할 수 있다.**
"""
from __future__ import annotations

import base64
import json
import logging

import anthropic

from src import config

logger = logging.getLogger(__name__)


def ask_claude(jpgs: list[bytes], model: str, prompt: str, schema: dict) -> dict:
    """줄여 둔 JPEG(앞뒤 여러 장도 된다)를 보내 판정 JSON 과 사용량을 받는다.

    실패하면 RuntimeError, 연결이 끊기면 ConnectionError.
    """
    key = config.ANTHROPIC_API_KEY
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY 가 없습니다.")

    req: dict = {
        "model": model,
        "max_tokens": 16000,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                             "data": base64.standard_b64encode(j).decode()}}
                for j in jpgs
            ] + [{"type": "text", "text": prompt}],
        }],
        # 보고 옮기는 일이라 깊은 추론이 필요 없다. OpenAI 쪽 reasoning low 와 맞춘다.
        "output_config": {"effort": "low",
                          "format": {"type": "json_schema", "schema": schema}},
    }
    # 안전 분류기가 거절하면 서버가 다른 모델로 다시 돌려준다. Haiku 는 이 기능이 없다.
    if not model.startswith("claude-haiku"):
        req["betas"] = ["server-side-fallback-2026-07-01"]
        req["fallbacks"] = "default"

    client = anthropic.Anthropic(api_key=key, timeout=60, max_retries=2)
    try:
        res = client.beta.messages.create(**req)
    except anthropic.APIStatusError as exc:
        # 오류 **종류와 이유만** 남긴다(요청 본문은 남기지 않는다). 이유가 있어야 원인을 안다 —
        # 실제로 크레딧이 바닥났을 때 상태 코드(400)만 찍혀서 코드 문제로 오해할 뻔했다.
        err = (exc.body or {}).get("error", {}) if isinstance(exc.body, dict) else {}
        logger.warning("Claude 응답 %s %s: %s", exc.status_code, err.get("type", ""),
                       str(err.get("message", ""))[:160])
        raise RuntimeError(f"HTTP {exc.status_code}") from exc
    except anthropic.APIConnectionError as exc:
        # 호출한 쪽이 "사진을 줄여 다시" 를 고를 수 있게 연결 실패는 따로 낸다.
        logger.warning("Claude 연결 실패: %s", type(exc).__name__)
        raise ConnectionError(type(exc).__name__) from exc

    if res.stop_reason == "refusal":
        raise RuntimeError("refusal")
    text = next((b.text for b in res.content if b.type == "text"), "")
    data = json.loads(text)
    data["usage"] = {"model": res.model,
                     "input": res.usage.input_tokens,
                     "output": res.usage.output_tokens}
    return data
