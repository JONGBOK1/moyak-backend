"""알약 인식 — 네트워크·DB 없이 확인할 수 있는 부분."""
from __future__ import annotations

import pytest

from src import config
from src.pills import search, vision, vision_llm
from src.pills.vision import PillVision


class TestTokens:
    def test_split_line_letters_are_joined(self):
        # DB 'A분할선C' 는 'AC' 로 비교한다. 'A C' 로 읽혀도 'AC' 가 만들어져야 한다.
        assert "AC" in search._tokens("A C")

    def test_adjacent_pairs_only(self):
        toks = search._tokens("M Z D")
        assert "MZ" in toks and "ZD" in toks
        assert "MD" not in toks

    def test_front_back_still_joined(self):
        assert search._tokens("D-W MPG") == ["DW", "MPG", "DWMPG"]

    def test_empty(self):
        assert search._tokens(None) == []


class TestToVision:
    def test_cleans_imprint_like_ocr(self):
        seen, imp = vision_llm.to_vision({
            "shape": "원형", "color": "노랑", "imprint": "lithium MFDS",
            "has_logo": False, "split_line": False, "aspect": 1.0, "confidence": 0.8,
        })
        assert imp == "LITHIUM"
        assert (seen.shape, seen.color, seen.aspect) == ("원형", "노랑", 1.0)

    def test_unknown_vocab_falls_back(self):
        seen, imp = vision_llm.to_vision({"shape": "별모양", "color": "금색", "imprint": None})
        assert (seen.shape, seen.color, imp) == ("기타", "하양", None)

    def test_has_logo_is_exposed(self):
        seen, _ = vision_llm.to_vision({"shape": "원형", "color": "하양", "imprint": None,
                                        "has_logo": True})
        assert seen.detail["has_logo"] is True


class TestAnalyzeBest:
    OLD = (PillVision(shape="장방형", color="하양", confidence=0.9), "OLD")

    @pytest.fixture(autouse=True)
    def _old_way(self, monkeypatch):
        monkeypatch.setattr(vision, "analyze_and_read", lambda raw: self.OLD)

    def test_uses_llm_first(self, monkeypatch):
        monkeypatch.setattr(config, "PILL_LLM_MODEL", "claude-opus-5-5")
        llm = (PillVision(shape="원형", color="노랑", confidence=0.8), "LLM")
        monkeypatch.setattr(vision_llm, "analyze_llm", lambda raw: llm)
        assert vision_llm.analyze_best(b"x") is llm

    def test_falls_back_when_llm_fails(self, monkeypatch):
        monkeypatch.setattr(config, "PILL_LLM_MODEL", "claude-opus-5-5")

        def boom(raw):
            raise vision_llm.LLMError("HTTP 529")
        monkeypatch.setattr(vision_llm, "analyze_llm", boom)
        seen, imp = vision_llm.analyze_best(b"x")
        assert imp == "OLD" and seen.detail["engine"] == "opencv"

    def test_empty_model_skips_llm(self, monkeypatch):
        monkeypatch.setattr(config, "PILL_LLM_MODEL", "")

        def never(raw):
            raise AssertionError("LLM 을 부르면 안 된다")
        monkeypatch.setattr(vision_llm, "analyze_llm", never)
        assert vision_llm.analyze_best(b"x")[1] == "OLD"

    def test_bad_image_is_not_swallowed(self, monkeypatch):
        # 이미지가 깨졌으면 예전 방식으로 떨어질 일이 아니라 400 이어야 한다.
        monkeypatch.setattr(config, "PILL_LLM_MODEL", "claude-opus-5-5")

        def bad(raw):
            raise ValueError("이미지를 읽을 수 없습니다.")
        monkeypatch.setattr(vision_llm, "analyze_llm", bad)
        with pytest.raises(ValueError):
            vision_llm.analyze_best(b"x")


class TestClaudeShrinkRetry:
    """Claude 연결이 끊기면 사진을 줄여 한 번 더 보낸다."""

    @staticmethod
    def _jpeg(w=1200, h=900) -> bytes:
        import cv2
        import numpy as np
        return cv2.imencode(".jpg", np.full((h, w, 3), 200, np.uint8))[1].tobytes()

    def test_retries_smaller_after_connection_error(self, monkeypatch):
        import cv2
        import numpy as np
        from src.pills import vision_claude

        sizes = []

        def fake(jpgs, model, prompt, schema):
            img = cv2.imdecode(np.frombuffer(jpgs[0], np.uint8), cv2.IMREAD_COLOR)
            sizes.append(max(img.shape[:2]))
            if len(sizes) == 1:
                raise ConnectionError("ReadError")
            return {"shape": "원형", "color": "하양", "imprint": None, "usage": {}}

        monkeypatch.setattr(vision_claude, "ask_claude", fake)
        got = vision_llm.ask_llm(self._jpeg(), model="claude-opus-5-5")
        assert got["shape"] == "원형"
        assert sizes == [vision_llm.MAX_SIDE, vision_llm.RETRY_SIDE]

    def test_gives_up_after_second_failure(self, monkeypatch):
        from src.pills import vision_claude

        def always(jpgs, model, prompt, schema):
            raise ConnectionError("ReadError")
        monkeypatch.setattr(vision_claude, "ask_claude", always)
        with pytest.raises(vision_llm.LLMError):
            vision_llm.ask_llm(self._jpeg(), model="claude-opus-5-5")


class _FakeSession:
    """search.find 가 던지는 SQL 은 무시하고 정해 둔 행만 돌려준다."""

    def __init__(self, rows):
        self.rows = rows

    def execute(self, *a, **k):
        return self

    def mappings(self):
        return self

    def all(self):
        return self.rows


def _row(seq, name, front):
    return {"item_seq": seq, "item_name": name, "entp_name": None, "drug_shape": "원형",
            "color_class1": "하양", "print_front": front, "print_back": None,
            "item_image": "x", "etc_otc_name": "일반의약품", "leng_long": None, "leng_short": None}


class TestLogoBonus:
    ROWS = [_row("1", "가나정", "AB"), _row("2", "하하정", "마크")]

    def test_logo_drug_ranks_first_when_logo_seen(self):
        got = search.find(_FakeSession(self.ROWS), shape="원형", color="하양", has_logo=True)
        assert [c.item_name for c in got] == ["하하정", "가나정"]
        assert "로고" in got[0].matched

    def test_no_bonus_without_logo(self):
        # 로고를 못 봤으면 둘은 동점이고 이름순이다.
        got = search.find(_FakeSession(self.ROWS), shape="원형", color="하양", has_logo=None)
        assert [c.item_name for c in got] == ["가나정", "하하정"]
        assert all("로고" not in c.matched for c in got)


class TestIdentify:
    """identify(): 1위가 확실하지 않을 때만 다시 고르고, 고른 순서를 앞에 둔다."""

    ROWS = [_row("1", "가나정", "AB"), _row("2", "다다정", "CD"), _row("3", "라라정", "EF")]

    @staticmethod
    def _claude(imprint=None):
        seen = PillVision(shape="원형", color="하양", confidence=0.8,
                          detail={"engine": "llm", "has_logo": False,
                                  "llm": {"usage": {"model": "claude-opus-5-5"}, "kinds": 1}})
        return lambda images: (seen, imprint)

    def test_reranks_when_not_decisive(self):
        from src.pills import identify
        r = identify.identify(_FakeSession(self.ROWS), [b"x"], analyzer=self._claude(),
                              reranker=lambda imgs, cands, seen: [2, 0])
        assert r.reranked
        assert [c.item_name for c in r.candidates] == ["라라정", "가나정", "다다정"]
        assert "사진 비교" in r.candidates[0].matched

    def test_no_rerank_when_imprint_decides(self):
        from src.pills import identify

        def never(*a):
            raise AssertionError("1위가 확실하면 다시 고르지 않는다")
        r = identify.identify(_FakeSession(self.ROWS), [b"x"], analyzer=self._claude("AB"),
                              reranker=never)
        assert not r.reranked and r.candidates[0].item_name == "가나정"

    def test_no_rerank_for_fallback_engine(self):
        from src.pills import identify
        seen = PillVision(shape="원형", color="하양", confidence=0.8, detail={"engine": "opencv"})

        def never(*a):
            raise AssertionError("Claude 가 아니면 다시 고르지 않는다")
        r = identify.identify(_FakeSession(self.ROWS), [b"x"], analyzer=lambda i: (seen, None),
                              reranker=never)
        assert r.engine == "opencv+google" and not r.reranked

    def test_mixed_pills_note(self):
        from src.pills import identify
        seen, _ = self._claude()([b"x"])
        seen.detail["llm"]["kinds"] = 2
        r = identify.identify(_FakeSession(self.ROWS), [b"x"], analyzer=lambda i: (seen, None),
                              reranker=lambda *a: None)
        assert any("서로 다른 약" in n for n in r.notes)


class TestDailyBudget:
    """서버 전체 하루 상한 — 넘으면 LLM 없이(예전 방식으로) 처리한다."""

    def test_stops_at_limit(self, monkeypatch):
        from src.pills import identify
        monkeypatch.setattr(config, "PILL_LLM_DAILY_LIMIT", 2)
        b = identify.DailyBudget()
        assert [b.take(), b.take(), b.take()] == [True, True, False]

    def test_identify_skips_llm_when_exhausted(self, monkeypatch):
        from src.pills import identify
        monkeypatch.setattr(config, "PILL_LLM_DAILY_LIMIT", 0)
        monkeypatch.setattr(identify, "budget", identify.DailyBudget())
        seen = PillVision(shape="원형", color="하양", confidence=0.8, detail={"engine": "opencv"})
        calls = []

        def fake_best(images, llm=True):
            calls.append(llm)
            return seen, None
        monkeypatch.setattr(identify, "analyze_best", fake_best)
        identify.identify(_FakeSession(TestIdentify.ROWS), [b"x"], reranker=lambda *a: None)
        assert calls == [False]

    def test_db_opened_only_for_search(self):
        """라우터는 연결 대신 연결을 여는 함수(read_only)를 넘긴다."""
        from contextlib import contextmanager

        from src.pills import identify
        opened = []

        @contextmanager
        def read_only():
            opened.append(1)
            yield _FakeSession(TestIdentify.ROWS)
        r = identify.identify(read_only, [b"x"], analyzer=TestIdentify._claude("AB"),
                              reranker=lambda *a: None)
        assert opened == [1] and r.candidates[0].item_name == "가나정" and r.confident
