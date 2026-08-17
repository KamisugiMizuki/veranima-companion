"""QQ 服务冒烟：验证反向 WS 端口监听 + bot loop 就绪。"""
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, "src")

from veranima.adapters.qq import QQAdapter
from veranima.core.agent import Agent
from veranima.core.character import CharacterCard
from veranima.core.state import AgentState
from veranima.llm.client import LLMClient
from veranima.memory.store import MemoryStore

PORT = 8099
result = {"ok": False, "loop": None}


def checker():
    for _ in range(50):
        try:
            s = socket.create_connection(("127.0.0.1", PORT), timeout=1)
            s.close()
            result["ok"] = True
            return
        except OSError:
            time.sleep(0.2)
    result["ok"] = False


tmp = tempfile.mkdtemp()
memory = MemoryStore(
    db_path=os.path.join(tmp, "smoke.db"),
    config={},
    llm_config={"base_url": "https://api.example.com/v1"},
)
agent = Agent(
    card=CharacterCard(name="小V", first_mes="hi"),
    memory=memory,
    llm=LLMClient({"base_url": "https://api.example.com/v1"}),
    state=AgentState(),
    config={"chat": {"proactive_message_prob": 0.0}},
)
a = QQAdapter(agent, ws_port=PORT, allowed_qq=["1"])
threading.Thread(target=checker, daemon=True).start()
# 主线程启动 WS 服务（quart 要求主线程）；常驻运行，由外部 kill 结束
a.run()
