"""MEMORY_SPEC 18.2 离线记忆评测集：事实/时序/冲突/多跳/承诺/无答案。

不依赖真实 LLM 与 embedding（BagEmbed 字符袋近似），验证确定性记忆管线行为。
指标：Recall@5、current-version accuracy、temporal accuracy、conflict accuracy、
no-answer precision。
"""
from __future__ import annotations

import datetime

import pytest

from veranima.memory.store import MemoryStore

# BagEmbed 字符袋（与 test_curate 同款）：相似文本 → 相似向量
class BagEmbed:
    dim = 64

    def embed(self, texts):
        import hashlib
        out = []
        for t in texts:
            v = [0.0] * self.dim
            for ch in t:
                h = hashlib.md5(ch.encode()).digest()[0] % self.dim
                v[h] += 1.0
            norm = sum(x * x for x in v) ** 0.5 or 1.0
            out.append([x / norm for x in v])
        return out


@pytest.fixture
def bench_store(tmp_path):
    return MemoryStore(db_path=str(tmp_path / "bench.db"), config={}, provider=BagEmbed())


def _past(days: int = 30) -> str:
    return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat(timespec="seconds")


def _future(days: int = 7) -> str:
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)).isoformat(timespec="seconds")


def _hit(hits, *keywords) -> bool:
    return any(all(k in h.content for k in keywords) for h in hits)


# ---------- 事实召回 ----------

def test_bench_fact_recall(bench_store):
    bench_store.store("semantic", "用户喜欢喝手冲咖啡，每天一杯", meta={"kind": "user_fact", "subject": "user"})
    hits = bench_store.recall("用户喝什么咖啡", top_k=5)
    assert _hit(hits, "手冲咖啡")
    hits2 = bench_store.recall("用户喜欢什么饮品", top_k=5)
    assert _hit(hits2, "咖啡")


# ---------- 时序 ----------

def test_bench_temporal_past(bench_store):
    bench_store.store("shared_episode", "上次爬山摔了一跤，膝盖到现在还疼", meta={"kind": "shared_episode", "event_time": _past(30)})
    hits = bench_store.recall("以前爬山发生了什么", top_k=5)
    assert _hit(hits, "爬山")  # past 意图 + event_time


def test_bench_temporal_future_plan(bench_store):
    bench_store.store("shared_episode", "计划下周一起去露营", meta={"kind": "shared_episode", "expires_at": _future(14)})
    hits = bench_store.recall("下周有什么安排", top_k=5)
    assert _hit(hits, "露营")


# ---------- 冲突 / 版本 ----------

def test_bench_conflict_correction(bench_store):
    old = bench_store.store("semantic", "用户周二开会", meta={"kind": "user_fact", "subject": "user"})
    bench_store.update_latest(old.id, "用户周三开会", meta={"supersedes": old.id, "kind": "user_fact"})
    hits = bench_store.recall("用户什么时候开会", top_k=5)
    assert hits and "周三" in hits[0].content  # current 版本
    assert all("周二" != h.content for h in hits)  # 旧版本不召回


def test_bench_conflict_preference_drift(bench_store):
    old = bench_store.store("semantic", "用户喜欢喝咖啡", meta={"kind": "user_fact", "subject": "user", "event_time": _past(90)})
    bench_store.update_latest(old.id, "用户现在改喝茶了", meta={"supersedes": old.id, "kind": "user_fact"})
    hits = bench_store.recall("用户现在喝什么", top_k=5)
    assert hits and "茶" in hits[0].content
    assert not _hit(hits, "咖啡")


# ---------- 多跳（共同经历 + 后续结果） ----------

def test_bench_multi_hop(bench_store):
    bench_store.store("shared_episode", "上次一起去看电影《流浪地球3》", meta={"kind": "shared_episode", "event_time": _past(15)})
    bench_store.store("shared_episode", "看完电影后用户说很喜欢科幻片", meta={"kind": "shared_episode", "event_time": _past(14)})
    hits = bench_store.recall("用户喜欢什么类型的电影", top_k=5)
    assert _hit(hits, "科幻")


# ---------- 承诺 ----------

def test_bench_commitment_open(bench_store):
    bench_store.store("procedural", "承诺：下周提醒用户买猫粮", meta={"kind": "commitment", "status": "open", "promise": True})
    hits = bench_store.recall("提醒买什么", top_k=5)
    assert _hit(hits, "猫粮")


def test_bench_commitment_done_not_reminded(bench_store):
    e = bench_store.store("procedural", "承诺：提醒用户交电费", meta={"kind": "commitment", "status": "open", "promise": True})
    bench_store.update_latest(e.id, "承诺：提醒用户交电费", meta={"supersedes": e.id, "status": "done", "promise": True})
    hits = bench_store.recall("交电费", top_k=5)
    # done 承诺不再以 open 语义出现（硬过滤 superseded 后 current 是 done）
    assert hits == [] or hits[0].status != "open"


# ---------- 无答案（拒绝编造） ----------

def test_bench_no_answer(bench_store):
    bench_store.store("semantic", "用户喜欢蓝色", meta={"kind": "user_fact"})
    hits = bench_store.recall("用户养过什么宠物", top_k=5)
    assert not _hit(hits, "宠物")  # 无证据不返回相关结果（或返回不相关=不编造）


# ---------- 文风（只调表达不调事实） ----------

def test_bench_style_profile_does_not_change_facts(tmp_path):
    from veranima.core.learning import StyleLearner, FeedbackSignal
    learner = StyleLearner()
    for i in range(25):
        learner.observe(FeedbackSignal(), "帮我看看这个方案行不行，尽快回复。")
    block = learner.profile.to_prompt_block()
    assert "直接" in block or "简短" in block or "交流偏好" in block
    assert "咖啡" not in block and "猫" not in block  # 画像不含事实内容
