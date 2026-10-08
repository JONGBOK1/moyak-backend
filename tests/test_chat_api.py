import pytest
from fastapi.testclient import TestClient
from src.api import demo
from src.api.routes import chat
from src import config


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(chat, 'resources', lambda app: (None, None, None))
    with_limits = demo.app.state.limiter.enabled
    demo.app.state.limiter.enabled = False
    c = TestClient(demo.app, base_url='http://127.0.0.1:8001', client=('127.0.0.1', 1234))
    yield c
    c.close()
    demo.app.state.limiter.enabled = with_limits


def test_chat_answer_history_and_evidence(client, monkeypatch):
    def ask(question, **kwargs):
        assert question == 'follow up'
        assert kwargs['history'] == [{'role': 'user', 'content': 'first question'}]
        return {'answer': 'test answer', 'sources': ['medicine'], 'evidence': [
            {'item_name': 'medicine', 'field': 'warning', 'field_label': 'warning', 'text': 'source text'}]}
    monkeypatch.setattr(chat, 'ask', ask)
    r = client.post('/chat', json={'question': 'follow up', 'history': [{'role': 'user', 'content': 'first question'}]})
    assert r.status_code == 200
    assert r.json()['answer'] == 'test answer'
    assert r.json()['evidence'][0]['text'] == 'source text'


def test_chat_failure_does_not_leak_provider_details(client, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError('private-connection-secret')
    monkeypatch.setattr(chat, 'ask', fail)
    r = client.post('/chat', json={'question': 'test'})
    assert r.status_code == 503
    assert 'private-connection-secret' not in r.text
    assert r.json()['detail']


def test_blank_question(client):
    assert client.post('/chat', json={'question': '   '}).status_code == 422
