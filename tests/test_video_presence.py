import requests

from src.consult import video


class FakeRes:
    def __init__(self, payload, status=200):
        self.payload, self.status = payload, status

    def raise_for_status(self):
        if self.status >= 400:
            raise requests.exceptions.HTTPError(str(self.status))

    def json(self):
        return self.payload


def test_participant_count_reads_total_count_and_caches(monkeypatch):
    monkeypatch.setattr(video.config, "DAILY_API_KEY", "test-key")
    video._PRESENCE_CACHE.clear()
    calls = []

    def fake_get(url, headers, timeout):
        calls.append(url)
        return FakeRes({"total_count": 1, "data": [{"userName": "약사"}]})

    monkeypatch.setattr(video.requests, "get", fake_get)
    assert video.room_participant_count("https://moyak.daily.co/abc123") == 1
    assert video.room_participant_count("https://moyak.daily.co/abc123") == 1  # 2초 캐시 → API 1회만
    assert calls == ["https://api.daily.co/v1/rooms/abc123/presence"]


def test_participant_count_none_without_key_or_on_error(monkeypatch):
    video._PRESENCE_CACHE.clear()
    monkeypatch.setattr(video.config, "DAILY_API_KEY", None)
    assert video.room_participant_count("https://moyak.daily.co/abc") is None
    monkeypatch.setattr(video.config, "DAILY_API_KEY", "test-key")
    monkeypatch.setattr(video.requests, "get", lambda *a, **k: FakeRes({}, status=500))
    assert video.room_participant_count("https://moyak.daily.co/err") is None
    assert video.room_participant_count(None) is None


def test_pharmacist_presence_does_not_infer_role_from_count_or_name(monkeypatch):
    monkeypatch.setattr(video.config, 'DAILY_API_KEY', 'test')
    data = {'total_count': 1, 'data': [{'userName': '약사', 'userId': 'patient'}]}
    monkeypatch.setattr(video.requests, 'get', lambda *a, **kw: FakeRes(data))
    assert video.pharmacist_presence('https://test.daily.co/room', 'p1') == (1, False)
    data['data'][0]['userId'] = video.pharmacist_identity('p1')
    assert video.pharmacist_presence('https://test.daily.co/room', 'p1') == (1, True)
    assert video.pharmacist_presence('https://test.daily.co/room', 'p2') == (1, False)
    assert video.pharmacist_presence('https://test.daily.co/room', None) == (1, False)
    data['data'] = None
    assert video.pharmacist_presence('https://test.daily.co/room', 'p1') is None


def test_token_binds_room_and_pharmacist_with_expiry(monkeypatch):
    monkeypatch.setattr(video.config, 'DAILY_API_KEY', 'test')
    def post(url, **kwargs):
        props = kwargs['json']['properties']
        assert props['room_name'] == 'room'
        assert props['user_id'] == video.pharmacist_identity('p1')
        assert len(props['user_id']) <= 36
        assert props['exp'] > video.time.time()
        return FakeRes({'token': 'bound-token'})
    monkeypatch.setattr(video.requests, 'post', post)
    assert video.pharmacist_token('https://test.daily.co/room', 'p1') == 'bound-token'
