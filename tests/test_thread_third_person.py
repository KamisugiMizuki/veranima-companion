"""B1：mind_threads 第三人称清洗——角色不该把用户叫「用户」。

09-08 实机实锤：存量画像键迁移值 `用户正在赶毕设改稿` 被裸发 5 次，且原样进
prompt 喂给模型（prompt_block 出口）。清洗放在 store 出口一次覆盖五个读取点。
"""

from __future__ import annotations

import hashlib

from veranima.memory.store import MemoryStore


class FakeEmbed:
    dim = 8

    def embed(self, texts):
        return [[b / 255 for b in hashlib.sha256(t.encode()).digest()[:8]] for t in texts]


def _mem(tmp_path) -> MemoryStore:
    return MemoryStore(db_path=str(tmp_path / "t.db"), config={}, provider=FakeEmbed())


def test_user_origin_topic_rewritten(tmp_path):
    mem = _mem(tmp_path)
    mem.thread_add("xumian", "用户正在赶毕设改稿", "user", intensity=0.9)
    rows = mem.thread_list("xumian")
    assert rows, "thread_add 应写入一行"
    assert rows[0]["topic"] == "你正在赶毕设改稿"


def test_non_user_origin_untouched(tmp_path):
    """角色自己的线（schedule/promise）不动——只有迁移来的 user 值带第三人称。"""
    mem = _mem(tmp_path)
    mem.thread_add("xumian", "用户的事跟我没关系", "schedule", intensity=0.5)
    rows = mem.thread_list("xumian")
    assert rows[0]["topic"] == "用户的事跟我没关系"
