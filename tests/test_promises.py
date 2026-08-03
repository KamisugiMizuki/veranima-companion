"""MVP2 承诺机制测试：识别 / 记录 / 检索注入 / 兑现标记。"""

from __future__ import annotations

import pytest

from veranima.core.promises import PromiseBook
from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        import hashlib
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


@pytest.fixture
def book(tmp_path):
    m = MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())
    return PromiseBook(m)


def test_extract_promise_patterns(book):
    assert book.extract("明天早上八点记得提醒我开会") is not None
    assert book.extract("你记得提醒我吃药") is not None
    assert book.extract("周末记得提醒我买花") is not None
    assert book.extract("每天提醒我喝水") is not None
    assert book.extract("帮我记着下周三的会议") is not None


def test_plain_message_no_promise(book):
    assert book.extract("今天天气不错") is None
    assert book.extract("你吃饭了吗") is None


def test_record_and_open_promises(book):
    mid = book.record("明天记得提醒我带伞")
    assert mid is not None
    opens = book.open_promises()
    assert len(opens) == 1
    assert opens[0].meta.get("promise") is True
    assert opens[0].meta.get("status") == "open"


def test_to_prompt_block_injects_promise(book):
    book.record("下周记得提醒我交房租")
    block = book.to_prompt_block(query_hint="房租")
    assert "我答应过你的事" in block
    assert "房租" in block


def test_no_promise_no_block(book):
    assert book.to_prompt_block() == ""


def test_mark_done(book):
    mid = book.record("明天记得提醒我吃药")
    book.mark_done(mid)
    opens = book.open_promises()
    assert len(opens) == 0  # status 已改 done，不再计入开放承诺
