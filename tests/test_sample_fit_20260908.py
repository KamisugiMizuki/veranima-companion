"""09-08「面向样例编程 → 根因防治」回归。

每个用例对应一处「照着某次聊天/某次检索写死」的旧实现，断言的是通用规则，
不是原样例句——例句只是用例之一。
"""
from veranima.core.virtual_schedule import _looks_arrived


def test_arrived_accepts_generic_confirmations():
    """用户回答「到了吗」的任意短确认都算，不再只认白名单里的五个字面。"""
    for text in ("到了", "我到了", "已到", "算到了", "到了到了", "我到公司了", "刚到", "嗯", "嗯嗯", "对"):
        assert _looks_arrived(text), text


def test_arrived_rejects_negation_and_unrelated():
    for text in ("还没到", "没到", "在路上了", "马上到", "堵车", "我在写代码", ""):
        assert not _looks_arrived(text), text


def test_candidate_entities_filters_query_echo_generically():
    """查询回声按「与 query 比对」排除，不再写死「明日方舟」。"""
    from veranima.tools.search import SearchResult, _candidate_entities

    def item(title: str, snippet: str = "") -> SearchResult:
        return SearchResult(title=title, url=f"https://x/{abs(hash(title))}", snippet=snippet,
                            domain="x.com", engine="test", published_at=None, quality="medium")

    results = [
        item("《明日方舟》复刻活动开启"),
        item("《某新作》联动活动公告"),
        item("当前开启的活动一览"),
    ]
    got = _candidate_entities(results, "明日方舟最近有什么复刻活动")
    assert "明日方舟" not in got          # 查询回声
    assert "当前开启" not in got          # 通用页面词
    assert "某新作" in got                # 真·新实体照收


def test_moments_tail_pattern_catches_addressing_not_just_one_sample():
    """动态喊话检查：@/点名提问/求助都拦，不依赖「你在干嘛呢.*发出来」原话。"""
    from veranima.core.moments import _TAIL_PAT

    for text in ("你在干嘛呢，发出来给我看看", "@你 今天上线吗", "你知道吗，我今天", "在吗", "帮我查一下天气"):
        assert _TAIL_PAT.search(text), text
    assert not _TAIL_PAT.search("今天下班路上看到一只猫"), "独白不该误伤"


def test_hermes_code_task_marker_has_no_trailing_space():
    from veranima.tools.hermes_bridge import HermesExecutionBridge

    assert "git" in HermesExecutionBridge.CODE_TASK_MARKERS
    assert "git " not in HermesExecutionBridge.CODE_TASK_MARKERS
