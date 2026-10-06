import json

import httpx
import pytest
from openai import OpenAI

from src.consult import ai


def test_transcription_uses_sdk_and_rejects_empty_output(monkeypatch):
    def handler(request):
        assert request.url.path == "/v1/audio/transcriptions"
        assert b"audio.webm" in request.content and b"test-audio" in request.content
        return httpx.Response(200, json={"text": " "})
    monkeypatch.setattr(ai, "client", lambda: OpenAI(api_key="test", http_client=httpx.Client(
        transport=httpx.MockTransport(handler))))
    with pytest.raises(ValueError, match="Empty"):
        ai.transcribe("audio.webm", b"test-audio")


def test_structured_summary_uses_sdk_and_disables_response_storage(monkeypatch):
    content = dict(symptoms=["두통"], discussion=[], medication_guidance=[],
                   precautions=[], follow_up=[], needs_verification=[])
    def handler(request):
        assert request.url.path == "/v1/responses"
        body = json.loads(request.content)
        assert body["store"] is False
        assert body["text"]["format"]["strict"] is True
        assert body["text"]["format"]["schema"]["additionalProperties"] is False
        return httpx.Response(200, json={
            "id": "resp_test", "created_at": 1, "object": "response", "status": "completed",
            "model": "gpt-4o", "parallel_tool_calls": True, "tool_choice": "auto", "tools": [],
            "output": [{"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                        "content": [{"type": "output_text", "text": json.dumps(content), "annotations": []}]}],
        })
    monkeypatch.setattr(ai, "client", lambda: OpenAI(api_key="test", http_client=httpx.Client(
        transport=httpx.MockTransport(handler))))
    assert ai.summarize({"chat": []}) == content
