package io.github.kamisugimizuki.veranima

import android.util.Log
import com.chaquo.python.Python
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject

/**
 * 连发合并 worker（docs/android/TURN_MERGE_SPEC.md，2026-09-08 用户裁决）。
 *
 * 旧行为：ChatScreen.busy 同时是发送闸 / 指示器 / 回执等待 —— 整轮期间发不出第二条、
 * 发送键被转圈顶掉、1 输入 : 1 输出硬绑定。现在：发送永不阻塞，消息入队，
 * 每角色一个**进程级** worker 攒批：
 *   - 空闲时等 W=2s 静默窗口（窗口内新消息并入，起点不重置），总等待封顶 CAP=8s；
 *   - 窗口关闭 → 队列内全部消息合成一轮（bridge.chat_batch：逐条落库 + 一次 handle）；
 *   - worker 忙时新消息只入队、不打断，当前轮结束立刻开下一轮（用户已经等过了）。
 *
 * ponytail: 轮询 delay(200) 判窗口，不上 Channel/定时器；worker 挂 object 单例，
 * 不能挂 rememberCoroutineScope（切页会把队列一起取消）。
 */
object TurnQueue {
    const val WINDOW_MS = 2_000L
    const val CAP_MS = 8_000L

    data class Item(val text: String, val images: List<String> = emptyList())

    private val workers = mutableMapOf<String, RoleWorker>()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    @Synchronized
    fun of(role: String): RoleWorker = workers.getOrPut(role) { RoleWorker(role) }

    class RoleWorker internal constructor(private val role: String) {
        /** 指示器（“正在酝酿…”）：window + running 期间为 true */
        val thinking = MutableStateFlow(false)
        /** 每轮结束 +1 → 会话页重载历史（回复不直接回到页面，页面可能已销毁） */
        val revision = MutableStateFlow(0L)
        val error = MutableStateFlow("")

        private val lock = Any()
        private val pending = ArrayDeque<Item>()
        private var lastEnqueueAt = 0L
        private var running = false
        private var seq = 0L

        fun enqueue(item: Item) {
            var start = false
            synchronized(lock) {
                pending.addLast(item)
                lastEnqueueAt = System.currentTimeMillis()
                if (!running) {
                    running = true
                    start = true
                }
                thinking.value = true
                seq += 1
                Log.i("VeranimaTurn", "[$role] enqueue #$seq len=${item.text.length} " +
                    "pending=${pending.size} running=$running")
            }
            if (start) startWorker()
        }

        private fun startWorker(): Job = scope.launch {
            try {
                var firstBatch = true
                while (true) {
                    // 首个批次等静默窗口；后续批次是 turn 期间攒下的，用户已经等过，直接跑
                    if (firstBatch) {
                        waitQuietWindow()
                        firstBatch = false
                    }
                    var taken: List<Item>? = null
                    synchronized(lock) {
                        if (pending.isNotEmpty()) {
                            taken = pending.toList()
                            pending.clear()
                        }
                    }
                    val batch = taken ?: break
                    Log.i("VeranimaTurn", "[$role] turn start: ${batch.size} msg")
                    val t0 = System.currentTimeMillis()
                    runBatch(batch)
                    Log.i("VeranimaTurn", "[$role] turn done in ${System.currentTimeMillis() - t0}ms")
                }
            } catch (t: Throwable) {
                error.value = "chat 失败: ${t.message ?: t.javaClass.simpleName}"
            } finally {
                // 退出与「退出瞬间新消息入队」在同一把锁里结算：有剩就原地续跑，
                // 免得旧 worker 的 finally 把新 worker 的 running 踩掉（双 worker 并发 handle）
                val restart: Boolean
                synchronized(lock) {
                    restart = pending.isNotEmpty()
                    running = restart
                    thinking.value = restart
                }
                if (restart) startWorker()
            }
        }

        private suspend fun waitQuietWindow() {
            val start = System.currentTimeMillis()
            while (true) {
                val now = System.currentTimeMillis()
                val quiet = now - synchronized(lock) { lastEnqueueAt }
                if (quiet >= WINDOW_MS || now - start >= CAP_MS) {
                    Log.i("VeranimaTurn", "[$role] window closed quiet=${now - start}ms")
                    return
                }
                delay(minOf(WINDOW_MS - quiet, 200L).coerceAtLeast(20L))
            }
        }

        private suspend fun runBatch(batch: List<Item>) {
            val payload = JSONArray()
            batch.forEach { item ->
                payload.put(JSONObject().apply {
                    put("text", item.text)
                    put("images", JSONArray(item.images))
                })
            }
            val raw = withContext(Dispatchers.IO) {
                Python.getInstance().getModule("bridge")
                    .callAttr("chat_batch", payload.toString(), role).toString()
            }
            val out = JSONObject(raw)
            if (!out.optBoolean("ok")) {
                error.value = "chat 失败: ${out.optString("error")}"
                Log.e("VeranimaTurn", "[$role] chat_batch failed: ${out.optString("error")}")
                return
            }
            error.value = ""
            revision.value += 1
        }
    }
}
