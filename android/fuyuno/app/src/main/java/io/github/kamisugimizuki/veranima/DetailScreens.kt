package io.github.kamisugimizuki.veranima

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chaquo.python.Python
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Galaxy 黑白极简详情三页（2026-09-01 UI 重构设计稿 §三）。
 * 数据全部来自 bridge 只读接口（memory_stats / memories_list / memory_full /
 * relationship_trend / sleep_status）——展示层不编造：无数据处显示「—」。
 */

// ---------- 手画图标（项目惯例：不拉 material-icons-extended；纯黑线条） ----------
// 只用 moveTo/lineTo/close（PathBuilder 的 arc API 参数易错，圆形用 12 段折线近似）

private fun vectorPath(name: String, draw: androidx.compose.ui.graphics.vector.PathBuilder.() -> Unit): ImageVector =
    ImageVector.Builder(name = name, defaultWidth = 24.dp, defaultHeight = 24.dp,
        viewportWidth = 24f, viewportHeight = 24f).apply {
        // 项目惯例（android-compose-ui 技能）：Builder.apply 内 path(fill=, stroke=) DSL
        path(fill = null, stroke = SolidColor(Color.Black), strokeLineWidth = 1.6f, pathBuilder = draw)
    }.build()

/** 折线近似圆（segments=12 足够小图标看不出棱） */
private fun androidx.compose.ui.graphics.vector.PathBuilder.ring(cx: Float, cy: Float, r: Float, segments: Int = 12) {
    for (i in 0..segments) {
        val a = (i % segments) / segments.toDouble() * 2 * Math.PI
        val x = cx + r * Math.cos(a).toFloat()
        val y = cy + r * Math.sin(a).toFloat()
        if (i == 0) moveTo(x, y) else lineTo(x, y)
    }
}

internal val IconMemoryVault: ImageVector by lazy {
    vectorPath("memory_vault") {
        moveTo(4f, 5f); lineTo(20f, 5f); lineTo(20f, 9f); lineTo(4f, 9f); close()
        moveTo(5f, 9f); lineTo(19f, 9f); lineTo(19f, 19f); lineTo(5f, 19f); close()
        moveTo(10f, 12.5f); lineTo(14f, 12.5f)
    }
}

internal val IconBond: ImageVector by lazy {
    vectorPath("bond") {
        ring(9f, 14f, 5f); ring(15f, 14f, 5f)
    }
}

internal val IconMoon: ImageVector by lazy {
    // 月牙=外圆弧折线 + 内弧回钩折线组成的多边形（fill 而非 stroke：
    // 双描边圆套圆是「套圈」不是月牙——2026-09-01 自查修正）
    val pts = mutableListOf<Pair<Float, Float>>()
    val cx = 12f; val cy = 12f; val r = 8.2f
    // 外圈：从 -60° 顺时针画到 240°（留右侧缺口）
    for (deg in generateSequence(-60.0) { it + 15 }.takeWhile { it <= 240.0 }) {
        val a = Math.toRadians(deg)
        pts += (cx + r * Math.cos(a).toFloat()) to (cy + r * Math.sin(a).toFloat())
    }
    // 内圈回钩：圆心右偏、半径稍小，从 240° 回 -60°（凹弧）
    val ix = 15.5f; val iy = 12f; val ir = 7.6f
    for (deg in generateSequence(240.0) { it - 20 }.takeWhile { it >= -60.0 }) {
        val a = Math.toRadians(deg)
        pts += (ix + ir * Math.cos(a).toFloat()) to (iy + ir * Math.sin(a).toFloat())
    }
    ImageVector.Builder(name = "moon", defaultWidth = 24.dp, defaultHeight = 24.dp,
        viewportWidth = 24f, viewportHeight = 24f).apply {
        path(name = "moon_p", fill = SolidColor(Color.Black)) {
            pts.forEachIndexed { i, pt -> if (i == 0) moveTo(pt.first, pt.second) else lineTo(pt.first, pt.second) }
            close()
        }
    }.build()
}

// ---------- 时间/数字格式化工具 ----------

private val ISO_FMT = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX", Locale.US)
private val HM_FMT = SimpleDateFormat("HH:mm", Locale.US)
private val DAY_FMT = SimpleDateFormat("yyyy-MM-dd", Locale.US)

private fun parseIso(s: String): Date? {
    if (s.isEmpty()) return null
    runCatching { ISO_FMT.parse(s) }.getOrNull()?.let { return it }
    // 无时区后缀的 ISO（core 的 created_at 全带 +00:00，防御性兜底）
    runCatching { SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US).parse(s) }
        .getOrNull()?.let { return it }
    return null
}

private fun relTime(s: String): String {
    val d = parseIso(s) ?: return "—"
    val mins = (System.currentTimeMillis() - d.time) / 60_000
    // 紧凑输出（顶部统计三格半行宽不截断：数值≤4 字）
    return when {
        mins < 1 -> "刚刚"
        mins < 60 -> "${mins}分"
        mins < 60 * 24 -> "${mins / 60}时"
        else -> "${mins / (60 * 24)}天"
    }
}

/** 时间轴分组标签：今天/昨天/更早·MM-dd */
private fun dayLabel(s: String): String {
    val d = parseIso(s) ?: return "更早"
    val today = DAY_FMT.format(Date())
    val yesterday = DAY_FMT.format(Date(System.currentTimeMillis() - 86_400_000L))
    return when (DAY_FMT.format(d)) {
        today -> "今天"
        yesterday -> "昨天"
        else -> "更早·${s.take(10).drop(5)}"
    }
}

private fun fmtHm(s: String): String = parseIso(s)?.let { HM_FMT.format(it) } ?: "—"

/** 日程 activity_key → 人可读中文（characters/lin/virtual_schedule.json 池；未知原样） */
private fun actMap(key: String): String = mapOf(
    "wake_routine" to "晨间梳洗", "focused_practice" to "在专注做事", "reset" to "在路上",
    "personal_interest_a" to "在自己的爱好里", "personal_interest_b" to "在自己的爱好里",
    "quiet_rest" to "歇着", "sleep" to "睡眠中", "gap" to "在发呆间隙",
    // 许眠（异地恋人卡）
    "commute_transit" to "在通勤路上", "model_training_work" to "在跑训练",
    "late_takeout_dinner" to "在吃夜宵外卖", "meme_archiving" to "在收藏表情包",
    "video_with_you" to "在等你同步放映", "blog_browsing" to "在刷博客",
).getOrDefault(key, key)

private fun fmtThousand(n: Int): String = String.format("%,d", n)

private fun fmtDur(min: Int): String =
    if (min < 0) "—" else if (min < 60) "${min}m" else "${min / 60}h ${min % 60}m"

private suspend fun bridgeJson(name: String, vararg args: Any): JSONObject = try {
    val bridge = Python.getInstance().getModule("bridge")
    JSONObject(withContext(Dispatchers.IO) {
        bridge.callAttr(name, *args).toString()
    })
} catch (e: Exception) {
    JSONObject().put("ok", false).put("error", e.message ?: "bridge 异常")
}

@Composable
private fun ErrorOr(t: String) {
    val dark = androidx.compose.foundation.isSystemInDarkTheme()
    Text(t, color = gxTextSecondary(dark), fontSize = 13.sp, modifier = Modifier.padding(16.dp))
}

@Composable
private fun LoadingBlock() {
    Box(Modifier.fillMaxWidth().height(120.dp), contentAlignment = Alignment.Center) {
        Text("读取中…", color = Color(0xFF888888), fontSize = 13.sp)
    }
}

// ==================== 页面1：记忆库 Memory Vault ====================

@Composable
fun MemoryDetailScreen(onBack: () -> Unit) {
    val dark = androidx.compose.foundation.isSystemInDarkTheme()
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    var stats by remember { mutableStateOf<JSONObject?>(null) }
    var mems by remember { mutableStateOf<JSONArray?>(null) }
    var openId by remember { mutableStateOf<Int?>(null) }
    var detail by remember { mutableStateOf<JSONObject?>(null) }
    var reloadTick by remember { mutableStateOf(0) }

    suspend fun load() {
        stats = bridgeJson("memory_stats")
        mems = bridgeJson("memories_list", "", "", 200).optJSONArray("memories")
    }
    LaunchedEffect(reloadTick) { load() }
    // 条目点击 → 拉完整文本（列表 content 截断 120，弹窗要原文全量）
    LaunchedEffect(openId) {
        val id = openId ?: return@LaunchedEffect
        detail = bridgeJson("memory_full", id)
    }

    GalaxyPage(title = "记忆库", onBack = onBack) {
        val st = stats
        val arr = mems
        // 设计稿 §五.3：时间轴必须 LazyColumn——整页单一 LazyColumn（统计/环形图为头部 item）
        LazyColumn(Modifier.fillMaxSize()) {
            item {
                if (st == null) LoadingBlock()
                else if (!st.optBoolean("ok")) ErrorOr("读取失败：${st.optString("error")}")
                else {
                    Column {
                        // 顶部统计三格
                        GalaxyCard(modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                            Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
                                GalaxyBigNumber(fmtThousand(st.optInt("total")), "记忆块数", Modifier.weight(1f))
                                GalaxyBigNumber(
                                    if (st.optInt("dim") > 0) "${st.optInt("dim")} dims" else "—",
                                    "向量维度", Modifier.weight(1f))
                                GalaxyBigNumber(relTime(st.optString("last_updated")), "最后更新", Modifier.weight(1f))
                            }
                        }
                        // 中部：记忆密度分布环形图（长期/短期/待归档）
                        val longN = st.optInt("long_term")
                        val shortN = st.optInt("short_term")
                        val pendN = st.optInt("pending_archive")
                        GalaxyCard(modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                            Text("记忆密度分布", fontSize = 14.sp, fontWeight = FontWeight.Medium, color = gxTextPrimary(dark))
                            Spacer(Modifier.height(14.dp))
                            Row(verticalAlignment = Alignment.CenterVertically) {
                                GalaxyDonut(
                                    segments = listOf(
                                        "长期" to longN.toFloat(),
                                        "短期" to shortN.toFloat(),
                                        "待归档" to pendN.toFloat()),
                                    colors = listOf(AccentBlue, AccentSage, AccentTaupe),
                                    centerTop = fmtThousand(longN + shortN + pendN),
                                    centerBottom = "条",
                                )
                                Spacer(Modifier.width(24.dp))
                                Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                    GalaxyLegend("长期", "$longN", AccentBlue)
                                    GalaxyLegend("短期", "$shortN", AccentSage)
                                    GalaxyLegend("待归档", "$pendN", AccentTaupe)
                                }
                            }
                        }
                    }
                }
            }
            if (arr == null) {
                item { LoadingBlock() }
            } else {
                val items = (0 until arr.length()).map { arr.getJSONObject(it) }
                groupedSections(items).forEach { (day, list) ->
                    item(key = "head-$day") {
                        Text(day, fontSize = 12.sp, fontWeight = FontWeight.SemiBold,
                            color = gxTextSecondary(dark),
                            modifier = Modifier.padding(start = 20.dp, top = 14.dp, bottom = 6.dp))
                    }
                    items.forEach { m ->
                        item(key = "m-${m.optInt("id")}") {
                            MemoryTimelineRow(m, onClick = { openId = m.optInt("id") })
                        }
                    }
                }
                item { Spacer(Modifier.height(24.dp)) }
            }
        }
        // 详情弹窗（含删除入口——原设置页记忆管理的删除能力平移至此）
        openId?.let { id ->
            val d = detail
            AlertDialog(
                onDismissRequest = { openId = null },
                confirmButton = {
                    TextButton(onClick = {
                        scope.launch {
                            bridgeJson("memories_erase", id)
                            openId = null
                            reloadTick++
                        }
                    }) { Text("删除", color = AccentTaupe) }
                },
                dismissButton = { TextButton(onClick = { openId = null }) { Text("关闭") } },
                title = {
                    Text(if (d == null) "读取中…" else "${d.optString("layer")} · ${d.optString("category")}",
                        fontSize = 14.sp, fontWeight = FontWeight.Medium)
                },
                text = {
                    if (d == null || !d.optBoolean("ok")) {
                        Text(d?.optString("error", "读取失败") ?: "", fontSize = 13.sp)
                    } else {
                        Column {
                            Text(d.optString("content"), fontSize = 14.sp,
                                color = gxTextPrimary(dark), lineHeight = 22.sp)
                            Spacer(Modifier.height(10.dp))
                            Text("强度 ${d.optDouble("strength", 0.0)} · 重要性 ${d.optDouble("importance", 0.0)} · 更新于 ${relTime(d.optString("updated_at"))}",
                                fontSize = 11.sp, color = gxTextSecondary(dark))
                        }
                    }
                },
                shape = RoundedCornerShape(12.dp),
                containerColor = if (dark) GxDeepGray else GxWhite,
            )
        }
    }
}

/** 按 dayLabel 分组并排序：今天→昨天→更早（组内保持原序=updated 倒序） */
private fun groupedSections(items: List<JSONObject>): List<Pair<String, List<JSONObject>>> {
    val grouped = LinkedHashMap<String, MutableList<JSONObject>>()
    items.forEach { m ->
        val key = dayLabel(m.optString("created_at"))
        grouped.getOrPut(key) { mutableListOf() }.add(m)
    }
    val rank = mapOf("今天" to 0, "昨天" to 1)
    return grouped.entries.sortedBy { (k, v) ->
        (rank[k] ?: (2 + ((System.currentTimeMillis() -
            (parseIso(v.first().optString("created_at"))?.time ?: 0)) / 86_400_000L)).toInt())
    }.map { it.key to it.value as List<JSONObject> }
}

@Composable
private fun MemoryTimelineRow(m: JSONObject, onClick: () -> Unit) {
    val dark = androidx.compose.foundation.isSystemInDarkTheme()
    val layer = m.optString("layer")
    val isLong = layer == "core_profile" || layer == "semantic"
    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 3.dp)
            .background(if (dark) GxDeepGray else GxWhite, RoundedCornerShape(12.dp))
            .border1(dark)
            .clickable(onClick = onClick)
            .padding(horizontal = 14.dp, vertical = 10.dp),
    ) {
        Column(Modifier.weight(1f)) {
            Text(m.optString("content").take(20), fontSize = 14.sp, color = gxTextPrimary(dark),
                maxLines = 1, overflow = TextOverflow.Ellipsis)
            Spacer(Modifier.height(4.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                GalaxyTag(if (isLong) "#长期" else "#短期", if (isLong) AccentBlue else AccentSage)
                GalaxyTag("#${m.optString("category", "未分类")}", AccentTaupe)
            }
        }
        Text(fmtHm(m.optString("created_at")), fontSize = 12.sp, color = gxTextSecondary(dark))
    }
}

private fun Modifier.border1(dark: Boolean): Modifier =
    this.then(Modifier.border(1.dp, gxHairline(dark), RoundedCornerShape(12.dp)))

// ==================== 页面2：羁绊图谱 Relationship Metrics ====================

@Composable
fun RelationshipDetailScreen(onBack: () -> Unit, role: String = "") {
    val dark = androidx.compose.foundation.isSystemInDarkTheme()
    var data by remember { mutableStateOf<JSONObject?>(null) }
    LaunchedEffect(role) { data = bridgeJson("relationship_trend", role) }

    GalaxyPage(title = "羁绊图谱", onBack = onBack) {
        val d = data
        if (d == null) { LoadingBlock(); return@GalaxyPage }
        if (!d.optBoolean("ok")) { ErrorOr("读取失败：${d.optString("error")}"); return@GalaxyPage }
        val rel = d.optJSONObject("relationship") ?: JSONObject()
        val role = d.optString("role", "")
        val series = d.optJSONArray("series") ?: JSONArray()
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
            // 顶部角色状态区（无立绘版：圆形+名字首字母，纯黑线条勾勒）
            Column(Modifier.fillMaxWidth().padding(top = 28.dp),
                horizontalAlignment = Alignment.CenterHorizontally) {
                Box(contentAlignment = Alignment.Center, modifier = Modifier.size(108.dp)) {
                    Canvas(Modifier.size(108.dp)) {
                        drawCircle(color = gxTextPrimary(dark), radius = size.minDimension / 2 - 2f,
                            center = Offset(size.width / 2, size.height / 2),
                            style = Stroke(width = 2.5f, cap = StrokeCap.Round))
                    }
                    Text(role.take(1).ifEmpty { "V" }, fontSize = 40.sp, fontWeight = FontWeight.Bold,
                        color = gxTextPrimary(dark))
                }
                Spacer(Modifier.height(10.dp))
                Text(d.optString("stage", "—"), fontSize = 16.sp, fontWeight = FontWeight.Bold, color = AccentBlue)
                Text(role, fontSize = 12.sp, color = gxTextSecondary(dark))
            }
            Spacer(Modifier.height(22.dp))
            // 中部：三大指标环（亲密/信任/理解；点缀色区分）
            fun dimAt(idx: Int, key: String): Double? {
                if (idx < 0 || idx >= series.length()) return null
                return series.getJSONObject(idx).optJSONObject("dims")
                    ?.takeIf { it.has(key) }?.optDouble(key)
            }
            fun delta(key: String): Int? {
                if (series.length() < 2) return null
                val a = dimAt(series.length() - 2, key) ?: return null
                val b = dimAt(series.length() - 1, key) ?: return null
                return ((b - a) * 100).toInt()
            }
            GalaxyCard(modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
                    GalaxyRing(rel.optDouble("intimacy", 0.0).toFloat(), "亲密度", AccentBlue)
                    GalaxyRing(rel.optDouble("trust", 0.0).toFloat(), "信任", AccentSage)
                    GalaxyRing(rel.optDouble("familiarity", 0.0).toFloat(), "理解", AccentTaupe)
                }
                Spacer(Modifier.height(14.dp))
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
                    GalaxyTrend(delta("intimacy"))
                    GalaxyTrend(delta("trust"))
                    GalaxyTrend(delta("familiarity"))
                }
            }
            Spacer(Modifier.height(16.dp))
            // 近 7 日折线（黑白灰线条；快照不足 2 天诚实提示）
            GalaxyCard(modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
                Text("近 7 日亲密度波动", fontSize = 14.sp, fontWeight = FontWeight.Medium, color = gxTextPrimary(dark))
                Spacer(Modifier.height(8.dp))
                val vals = (0 until series.length()).mapNotNull { i ->
                    series.getJSONObject(i).optJSONObject("dims")?.optDouble("intimacy")?.toFloat()
                }
                if (vals.size >= 2) {
                    GalaxySparkline(vals.takeLast(7))
                    Row(Modifier.fillMaxWidth().padding(top = 4.dp),
                        horizontalArrangement = Arrangement.SpaceBetween) {
                        (maxOf(0, vals.size - 7) until vals.size).map { series.getJSONObject(it).optString("day").take(5) }
                            .forEach { Text(it, fontSize = 10.sp, color = gxTextSecondary(dark)) }
                    }
                } else {
                    Text("每日快照积累中，暂不可绘趋势", fontSize = 12.sp, color = gxTextSecondary(dark),
                        modifier = Modifier.padding(vertical = 12.dp))
                }
            }
            // 其余维度（全七维诚实展示；主色黑线）
            GalaxyCard(modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 16.dp)) {
                Text("全部维度", fontSize = 14.sp, fontWeight = FontWeight.Medium, color = gxTextPrimary(dark))
                Spacer(Modifier.height(10.dp))
                listOf("reciprocity" to "互惠", "safety" to "安全感",
                    "conflict_tension" to "冲突张力", "repair_progress" to "修复进度").forEach { (k, label) ->
                    val v = rel.optDouble(k, 0.0).toFloat().coerceIn(0f, 1f)
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 6.dp)) {
                        Text(label, fontSize = 12.sp, color = gxTextSecondary(dark), modifier = Modifier.width(64.dp))
                        Box(Modifier.weight(1f).height(4.dp).background(
                            if (dark) GxNightHairline else Color(0xFFE8E8E8), RoundedCornerShape(2.dp))) {
                            Box(Modifier.fillMaxWidth(v).height(4.dp)
                                .background(gxTextPrimary(dark), RoundedCornerShape(2.dp)))
                        }
                        Text("${(v * 100).toInt()}%", fontSize = 11.sp, color = gxTextSecondary(dark),
                            modifier = Modifier.width(36.dp), textAlign = TextAlign.End)
                    }
                }
            }
        }
    }
}

// ==================== 页面3：睡眠报告 Sleep Monitor ====================

@Composable
fun SleepDetailScreen(onBack: () -> Unit) {
    val dark = androidx.compose.foundation.isSystemInDarkTheme()
    var data by remember { mutableStateOf<JSONObject?>(null) }
    LaunchedEffect(Unit) { data = bridgeJson("sleep_status") }

    GalaxyPage(title = "睡眠报告", onBack = onBack) {
        val d = data
        if (d == null) { LoadingBlock(); return@GalaxyPage }
        if (!d.optBoolean("ok")) { ErrorOr("读取失败：${d.optString("error")}"); return@GalaxyPage }
        val asleep = d.optBoolean("asleep")
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) {
            // 顶部实时状态胶囊（睡眠中=深灰底白字呼吸脉冲；清醒=白底黑字）
            Box(Modifier.fillMaxWidth().padding(top = 24.dp), contentAlignment = Alignment.Center) {
                BreathingPill(if (asleep) "深度睡眠中 💤" else "已唤醒 ☀️", night = asleep)
            }
            Spacer(Modifier.height(22.dp))
            // 中部：睡眠总时长 + 质量评分
            val last = d.optJSONObject("last")
            GalaxyCard(modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
                Text(if (asleep) "已入睡" else "最近一次睡眠", fontSize = 12.sp, color = gxTextSecondary(dark))
                Spacer(Modifier.height(2.dp))
                val minutes =
                    if (asleep) d.optInt("current_minutes", -1)
                    else last?.optInt("sleep_minutes", -1) ?: -1
                Text(fmtDur(minutes), fontSize = 44.sp, fontWeight = FontWeight.Bold, color = gxTextPrimary(dark))
                when {
                    asleep -> Text("睡眠进行中，时长实时累计", fontSize = 11.sp, color = gxTextSecondary(dark))
                    last != null && last.has("score") -> {
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 2.dp)) {
                            Text("睡眠质量评分：", fontSize = 12.sp, color = gxTextSecondary(dark))
                            Text("${last.optInt("score")} 分", fontSize = 14.sp,
                                fontWeight = FontWeight.Bold, color = AccentTaupe)
                        }
                        Text("基于睡眠时长与入睡时刻估算", fontSize = 10.sp, color = gxTextSecondary(dark))
                    }
                    else -> Text("还没有已闭合的睡眠记录——对我说「我睡了」「醒了」就会开始记录。",
                        fontSize = 12.sp, color = gxTextSecondary(dark))
                }
                if (last != null) {
                    Spacer(Modifier.height(10.dp))
                    Text("${fmtHm(last.optString("fell_asleep_at"))} 入睡 —— ${fmtHm(last.optString("woke_at"))} 醒来",
                        fontSize = 14.sp, fontWeight = FontWeight.Medium, color = gxTextPrimary(dark))
                    if (last.optString("summary").isNotEmpty()) {
                        Spacer(Modifier.height(6.dp))
                        Text(last.optString("summary"), fontSize = 12.sp, color = gxTextSecondary(dark),
                            lineHeight = 18.sp)
                    }
                }
            }
            Spacer(Modifier.height(16.dp))
            // 底部：分段分布条（设备无脑电分期→按实际入睡/苏醒绘占比；诚实文案注明）
            val cycles = d.optJSONArray("cycles") ?: JSONArray()
            GalaxyCard(modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp)) {
                val shown = minOf(cycles.length(), 7)
                Text("作息分布（近 $shown 天）", fontSize = 14.sp, fontWeight = FontWeight.Medium,
                    color = gxTextPrimary(dark))
                Spacer(Modifier.height(4.dp))
                Text("设备未接睡眠分期监测，按你报告的入睡/苏醒绘实际占比",
                    fontSize = 10.sp, color = gxTextSecondary(dark))
                if (shown == 0) {
                    Text("暂无记录", fontSize = 12.sp, color = gxTextSecondary(dark),
                        modifier = Modifier.padding(vertical = 12.dp))
                }
                // cycles 已按入睡时刻倒序；图表要时序正序
                val recent = (0 until shown).map { cycles.getJSONObject(it) }.reversed()
                recent.forEach { c ->
                    val f = parseIso(c.optString("fell_asleep_at"))
                    val w = parseIso(c.optString("woke_at"))
                    val sleepMin = if (f != null && w != null)
                        ((w.time - f.time) / 60_000L).toInt().coerceIn(0, 24 * 60) else -1
                    Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 8.dp)) {
                        Text(fmtHm(c.optString("fell_asleep_at")), fontSize = 10.sp,
                            color = gxTextSecondary(dark), modifier = Modifier.width(40.dp))
                        // 分段条：睡眠段（≥7h 雾霾蓝 / <7h 暖灰褐）其余留底=清醒
                        Box(Modifier.weight(1f).height(10.dp).background(
                            if (dark) GxNightHairline else Color(0xFFEFEFEF), RoundedCornerShape(5.dp))) {
                            if (sleepMin > 0) {
                                Box(Modifier.fillMaxWidth(sleepMin / (24f * 60)).fillMaxHeight()
                                    .background(if (sleepMin >= 420) AccentBlue else AccentTaupe,
                                        RoundedCornerShape(5.dp)))
                            }
                        }
                        Text(when {
                                w != null -> fmtDur(sleepMin)          // 已闭合：0 分也显示实际值
                                f != null -> "睡眠中"                   // 未闭合才是在睡
                                else -> "—"
                            }, fontSize = 10.sp,
                            color = gxTextSecondary(dark), modifier = Modifier.width(46.dp),
                            textAlign = TextAlign.End)
                    }
                }
                Spacer(Modifier.height(12.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(14.dp)) {
                    GalaxyLegend("睡眠≥7h", "", AccentBlue)
                    GalaxyLegend("睡眠<7h", "", AccentTaupe)
                    GalaxyLegend("清醒", "", if (dark) GxNightHairline else Color(0xFFEFEFEF))
                }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}
