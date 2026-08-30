package io.github.kamisugimizuki.veranima

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.chaquo.python.Python
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** 设置页：表单直连 bridge（写 config.yaml，改动后点右上"重启生效"）。
 *  导入/导出走 SAF 系统文件选择器（GetContent/OpenDocument），不再依赖 inbox 约定。 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(onBack: () -> Unit) {
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current
    var s by remember { mutableStateOf<JSONObject?>(null) }
    var dirty by remember { mutableStateOf(false) }
    var activeChar by remember { mutableStateOf("") }
    var chars by remember { mutableStateOf(listOf<String>()) }

    suspend fun reload() {
        val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("get_settings").toString() })
        if (!o.optBoolean("ok")) { snackbar.showSnackbar("读取失败: ${o.optString("error")}"); return }
        s = o
        activeChar = o.getString("active_character")
        chars = o.getJSONArray("characters").let { a -> (0 until a.length()).map { a.getString(it) } }
    }
    LaunchedEffect(Unit) { reload() }

    fun report(r: JSONObject, name: String) {
        scope.launch {
            snackbar.showSnackbar(if (r.optBoolean("ok")) "$name ✓ ${r.optString("detail")}"
                                  else "$name 失败: ${r.optString("error")}")
        }
        if (r.optBoolean("restart_required")) dirty = true
    }
    fun set(key: String, value: String) = scope.launch {
        report(JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("set_setting", key, value).toString() }), key)
    }
    fun act(name: String, arg: String? = null) = scope.launch {
        val o = JSONObject(withContext(Dispatchers.IO) {
            if (arg == null) bridge.callAttr(name) else bridge.callAttr(name, arg)
        }.toString())
        if (o.has("path")) o.put("detail", o.getString("path"))
        if (o.has("memories")) o.put("detail", "memories=" + o.getInt("memories"))
        report(o, name)
    }
    // 连通性测试：bridge.test_conn 真调一次远端（读 config.yaml 现值，保存后即可测）
    val busy = remember { mutableStateOf("") }
    fun testConn(which: String) = scope.launch {
        busy.value = which
        val o = try {
            JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("test_conn", which).toString() })
        } catch (e: Exception) { JSONObject().put("ok", false).put("error", e.message ?: "bridge 异常") }
        busy.value = ""
        report(o, when (which) { "llm" -> "语言模型" "vision" -> "视觉模型" "embedding" -> "Embedding" else -> "搜索" })
    }
    // SAF：导出=用户选完目标后，bridge 现场生成 inbox 文件再拷过去（顺序=选→生成→拷贝，无竞态）
    val cr = ctx.contentResolver
    var pendingRole by remember { mutableStateOf<String?>(null) }
    fun copyOut(name: String, uri: android.net.Uri): Long {
        val src = java.io.File(ctx.filesDir, "inbox/$name")
        cr.openOutputStream(uri)?.use { out -> src.inputStream().use { inp -> inp.copyTo(out) } }
        return src.length()
    }
    fun stageIn(uri: android.net.Uri, name: String) {
        val dst = java.io.File(ctx.filesDir, "inbox"); dst.mkdirs()
        cr.openInputStream(uri)?.use { inp -> dst.resolve(name).outputStream().use { out -> inp.copyTo(out) } }
    }
    val exportBackup = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        if (uri != null) scope.launch {
            val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("backup_export").toString() })
            if (!o.optBoolean("ok")) { snackbar.showSnackbar("备份生成失败: ${o.optString("error")}"); return@launch }
            val n = withContext(Dispatchers.IO) { copyOut("backup_out.zip", uri) }
            snackbar.showSnackbar("记忆备份已导出 ${n / 1024}KB")
        }
    }
    val importBackup = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) scope.launch {
            withContext(Dispatchers.IO) { stageIn(uri, "backup.zip") }
            act("backup_import")
        }
    }
    val exportRole = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        if (uri != null) scope.launch {
            val role = pendingRole ?: return@launch
            val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("role_export", role).toString() })
            if (!o.optBoolean("ok")) { snackbar.showSnackbar("角色包生成失败: ${o.optString("error")}"); return@launch }
            val n = withContext(Dispatchers.IO) { copyOut("role_pending.char", uri) }
            snackbar.showSnackbar("角色包已导出 ${n / 1024}KB")
        }
    }
    val importRole = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) scope.launch {
            withContext(Dispatchers.IO) { stageIn(uri, "pending.char") }
            act("role_import")
        }
    }

    Scaffold(snackbarHost = { SnackbarHost(snackbar) },
             containerColor = Canvas,
             topBar = {
        // 聊天页风格统一（2026-08-29）：衬线页头、无灰底；珊瑚只留给「重启生效」
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("返回", color = Muted) }
            Text("设置", style = MaterialTheme.typography.headlineSmall)
            Spacer(Modifier.weight(1f))
            if (dirty) Button(onClick = { restartApp(ctx) },
                colors = ButtonDefaults.buttonColors(Coral),
                shape = MaterialTheme.shapes.small) { Text("重启生效") }
        }
    }) { pad ->
        Column(Modifier.padding(pad).padding(horizontal = 16.dp).verticalScroll(rememberScrollState())) {
            val st = s
            if (st == null) { Text("读取设置中…", color = Muted); return@Column }
            val f = st.getJSONObject("fields")

            SectionCard("LLM") {
                val k1 = remember { mutableStateOf("") }
                val k2 = remember { mutableStateOf(f.getString("llm_base_url")) }
                val k3 = remember { mutableStateOf(f.getString("llm_model")) }
                val k4 = remember { mutableStateOf(f.getString("llm_vision_model")) }
                CommitRow("API Key（当前 ${f.getString("llm_api_key")}；留空保存=保持不变）", "", true, k1)
                CommitRow("Base URL", f.getString("llm_base_url"), false, k2)
                CommitRow("模型名", f.getString("llm_model"), false, k3)
                CommitRow("视觉模型名（发图时用；留空=不支持发图）", f.getString("llm_vision_model"), false, k4)
                SaveButton("保存 LLM 配置") {
                    set("llm_api_key", k1.value)
                    set("llm_base_url", k2.value)
                    set("llm_model", k3.value)
                    set("llm_vision_model", k4.value)
                }
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    TestButton("llm", "测语言模型", busy.value) { testConn(it) }
                    TestButton("vision", "测视觉模型", busy.value) { testConn(it) }
                }
            }

            SectionCard("Embedding（记忆召回的语义向量，安卓走远程 API，必填）") {
                val k1 = remember { mutableStateOf("") }
                val k2 = remember { mutableStateOf(f.getString("embedding_base_url")) }
                val k3 = remember { mutableStateOf(f.getString("embedding_model")) }
                CommitRow("API Key（当前 ${f.getString("embedding_api_key")}；留空保存=保持不变）", "", true, k1)
                CommitRow("Base URL", f.getString("embedding_base_url"), false, k2)
                CommitRow("模型名", f.getString("embedding_model"), false, k3)
                SaveButton("保存 Embedding 配置") {
                    set("embedding_api_key", k1.value)
                    set("embedding_base_url", k2.value)
                    set("embedding_model", k3.value)
                }
                TestButton("embedding", "测 Embedding", busy.value) { testConn(it) }
            }

            SectionCard("联网搜索（博查 Bocha，安卓唯一后端）") {
                val k1 = remember { mutableStateOf("") }
                val k2 = remember { mutableStateOf(f.getString("search_base_url")) }
                CommitRow("API Key（当前 ${f.getString("search_api_key")}；留空保存=保持不变）", "", true, k1)
                CommitRow("Base URL", f.getString("search_base_url"), false, k2)
                SaveButton("保存搜索配置") {
                    set("search_api_key", k1.value)
                    set("search_base_url", k2.value)
                }
                TestButton("search", "测搜索", busy.value) { testConn(it) }
            }

            SectionCard("角色（点名字切换；导出=轻量 .char 不含立绘语音）") {
                chars.forEach { c ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        RadioButton(selected = activeChar == c, onClick = { activeChar = c; set("active_character", c) })
                        Text(c, Modifier.padding(end = 8.dp))
                        TextButton(onClick = { pendingRole = c; exportRole.launch("veranima-role-$c.char") }) { Text("导出…", color = Muted) }
                    }
                }
                TextButton(onClick = { importRole.launch("*/*") }) { Text("导入角色包（.char）", color = Muted) }
            }

            SectionCard("共享记忆备份（导出=全部角色的共同记忆 zip；导入=全量覆盖）") {
                Row {
                    Button(onClick = { exportBackup.launch("veranima-backup.zip") },
                        colors = ButtonDefaults.buttonColors(Coral), shape = MaterialTheme.shapes.small) { Text("导出…") }
                    Spacer(Modifier.width(12.dp))
                    Button(onClick = { importBackup.launch("application/zip") },
                        colors = ButtonDefaults.buttonColors(Coral), shape = MaterialTheme.shapes.small) { Text("导入…") }
                }
            }

            // ---- DESIGN §11-A 性格成长树（只读：关系七维+阶段 / 风格四维 / 技能点 / 承诺） ----
            SectionCard("性格成长（关系阶段 / 风格画像 / 学会的规矩）") {
                var g by remember { mutableStateOf<JSONObject?>(null) }
                suspend fun loadGrowth() {
                    g = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("growth_report").toString() })
                }
                LaunchedEffect(Unit) { loadGrowth() }
                val gd = g
                if (gd == null || !gd.optBoolean("ok")) {
                    Text("读取中…", style = MaterialTheme.typography.bodySmall, color = Muted)
                } else {
                    Text("阶段：${gd.optString("stage")}", style = MaterialTheme.typography.titleMedium)
                    Spacer(Modifier.height(4.dp))
                    val rel = gd.optJSONObject("relationship") ?: JSONObject()
                    // 关系七维进度条（只读：core 确定性更新，UI 不手调）
                    listOf("trust" to "信任", "familiarity" to "熟悉", "intimacy" to "亲密",
                           "reciprocity" to "互惠", "safety" to "安全感",
                           "conflict_tension" to "冲突张力", "repair_progress" to "修复进度"
                    ).forEach { (k, label) ->
                        Row(verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.padding(top = 3.dp)) {
                            Text(label, style = MaterialTheme.typography.bodySmall, color = Muted,
                                modifier = Modifier.width(64.dp))
                            Box(Modifier.weight(1f).height(6.dp).background(Hairline, RoundedCornerShape(3.dp))) {
                                Box(Modifier.fillMaxWidth(rel.optDouble(k, 0.0).toFloat().coerceIn(0f, 1f))
                                    .height(6.dp).background(Coral, RoundedCornerShape(3.dp)))
                            }
                            Text("${(rel.optDouble(k, 0.0) * 100).toInt()}%",
                                style = MaterialTheme.typography.labelSmall, color = MutedSoft,
                                modifier = Modifier.width(40.dp), textAlign = androidx.compose.ui.text.style.TextAlign.End)
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    // 风格画像（四维）
                    val st = gd.optJSONObject("style") ?: JSONObject()
                    Text("文风：回复 ${st.optString("reply_length", "—")} / 语气 ${st.optString("formality", "—")} / 幽默 ${st.optString("humor", "—")} / 话题跟随 ${st.optString("topic_follow", "—")}",
                        style = MaterialTheme.typography.bodySmall, color = Muted)
                    Spacer(Modifier.height(8.dp))
                    // 技能点（procedural 规则）
                    val skills = gd.optJSONArray("skills") ?: org.json.JSONArray()
                    var skillShowAll by remember { mutableStateOf(false) }
                    Text("学会的规矩：${skills.length()} 条", style = MaterialTheme.typography.bodySmall, color = Muted)
                    val skillN = if (skillShowAll) skills.length() else minOf(skills.length(), 3)
                    for (i in 0 until skillN) {
                        val sk = skills.getJSONObject(i)
                        Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.padding(top = 2.dp)) {
                            Text("· ${sk.optString("kind")}：${sk.optString("content")}",
                                style = MaterialTheme.typography.labelSmall, color = MutedSoft,
                                modifier = Modifier.weight(1f))  // 自动换行（不截断）
                            TextButton(onClick = {
                                scope.launch {
                                    val r = JSONObject(withContext(Dispatchers.IO) {
                                        bridge.callAttr("memories_erase", sk.getInt("id")).toString() })
                                    report(r, "删除规矩")
                                    loadGrowth()
                                }
                            }) { Text("删除", color = Coral) }
                        }
                    }
                    if (skills.length() > 3) {
                        TextButton(onClick = { skillShowAll = !skillShowAll }, modifier = Modifier.padding(top = 2.dp)) {
                            Text(if (skillShowAll) "收起" else "展开全部 ${skills.length()} 条", color = Coral,
                                style = MaterialTheme.typography.labelSmall)
                        }
                    }
                    val pr = gd.optJSONArray("promises") ?: org.json.JSONArray()
                    if (pr.length() > 0) {
                        Text("未兑现承诺：${pr.length()} 条", style = MaterialTheme.typography.bodySmall,
                            color = Coral, modifier = Modifier.padding(top = 4.dp))
                    }
                }
            }

            // ---- DESIGN §11-B 记忆库管理（标签云 + 手动删除） ----
            SectionCard("记忆库（标签云选择分类；点删除移除不良记忆）") {
                var mem by remember { mutableStateOf<JSONObject?>(null) }
                var filter by remember { mutableStateOf("") }
                var memShowAll by remember { mutableStateOf(false) }
                fun loadMem() {
                    scope.launch {
                        mem = JSONObject(withContext(Dispatchers.IO) {
                            bridge.callAttr("memories_list", "", filter).toString() })
                    }
                }
                LaunchedEffect(Unit) { loadMem() }
                val md = mem
                if (md == null || !md.optBoolean("ok")) {
                    Text("读取中…", style = MaterialTheme.typography.bodySmall, color = Muted)
                } else {
                    val tags = md.optJSONObject("tags") ?: JSONObject()
                    val total = md.optJSONArray("memories")?.length() ?: 0
                    Text("共 $total 条记忆", style = MaterialTheme.typography.bodySmall, color = Muted)
                    Spacer(Modifier.height(6.dp))
                    // 标签云：字号/深浅随数量
                    val keys = tags.keys().asSequence().toList()
                    val maxN = keys.map { tags.optInt(it) }.maxOrNull() ?: 1
                    @OptIn(androidx.compose.foundation.layout.ExperimentalLayoutApi::class)
                    androidx.compose.foundation.layout.FlowRow(
                        horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        keys.forEach { k ->
                            val n = tags.optInt(k)
                            val sel = filter == k
                            Surface(
                                onClick = { filter = if (sel) "" else k; loadMem() },
                                color = if (sel) Coral else SurfaceCard,
                                shape = RoundedCornerShape(999.dp)) {
                                Text("$k $n",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = if (sel) Color.White else Ink,
                                    fontSize = (11 + 3 * n / maxN).sp,
                                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 3.dp))
                            }
                        }
                    }
                    Spacer(Modifier.height(6.dp))
                    val arr = md.optJSONArray("memories") ?: org.json.JSONArray()
                    val shownN = if (memShowAll) arr.length() else minOf(arr.length(), 20)
                    val shown = (0 until shownN).map { arr.getJSONObject(it) }
                    shown.forEach { m0 ->
                        Row(verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier.padding(top = 4.dp)) {
                            Column(Modifier.weight(1f)) {
                                Text("${m0.optString("layer")}·${m0.optString("category")}·强度${m0.optDouble("strength", 0.0)}",
                                    style = MaterialTheme.typography.labelSmall, color = MutedSoft)
                                Text(m0.optString("content"), style = MaterialTheme.typography.bodySmall,
                                    maxLines = 2, overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis)
                            }
                            TextButton(onClick = {
                                scope.launch {
                                    val r = JSONObject(withContext(Dispatchers.IO) {
                                        bridge.callAttr("memories_erase", m0.getInt("id")).toString() })
                                    report(r, "删除记忆")
                                    loadMem()
                                }
                            }) { Text("删除", color = Coral) }
                        }
                    }
                    if (arr.length() > 20) {
                        TextButton(onClick = { memShowAll = !memShowAll }, modifier = Modifier.padding(top = 2.dp)) {
                            Text(if (memShowAll) "收起" else "展开全部 ${arr.length()} 条", color = Coral,
                                style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            }
            // ---- 用户睡眠周期（2026-08-30 用户拍板：入睡/苏醒/时长记录 + 总结） ----
            SectionCard("睡眠记录（向我报告「睡了/醒了」自动记录；长睡眠苏醒有总结）") {
                var sc by remember { mutableStateOf<JSONObject?>(null) }
                LaunchedEffect(Unit) {
                    sc = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("sleep_cycles").toString() })
                }
                val scd = sc
                if (scd == null || !scd.optBoolean("ok")) {
                    Text("读取中…", style = MaterialTheme.typography.bodySmall, color = Muted)
                } else {
                    val arr = scd.optJSONArray("cycles") ?: org.json.JSONArray()
                    if (arr.length() == 0) {
                        Text("还没有睡眠记录——对我说「我睡了」「醒了」就会开始记录。",
                            style = MaterialTheme.typography.bodySmall, color = Muted)
                    }
                    fun fmtHm(iso: String): String = runCatching {
                        java.text.SimpleDateFormat("MM-dd HH:mm", java.util.Locale.getDefault()).format(
                            java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ssXXX",
                                java.util.Locale.US).parse(iso))
                    }.getOrDefault(iso.take(16))
                    fun fmtDur(min: Int): String = if (min <= 0) "—" else "${min / 60}小时${min % 60}分"
                    for (i in 0 until minOf(arr.length(), 6)) {
                        val c = arr.getJSONObject(i)
                        Column(Modifier.padding(top = 6.dp)) {
                            Text("入睡时刻：${fmtHm(c.optString("fell_asleep_at"))}；睡眠时长：${fmtDur(c.optInt("sleep_minutes"))}；" +
                                    "苏醒时刻：${if (c.optString("woke_at").isNotEmpty()) fmtHm(c.optString("woke_at")) else "（未报告）"}；" +
                                    "清醒时长：${fmtDur(c.optInt("awake_minutes"))}",
                                style = MaterialTheme.typography.labelSmall, color = MutedSoft)
                            if (c.optString("summary").isNotEmpty()) {
                                Text(c.optString("summary"), style = MaterialTheme.typography.bodySmall,
                                    color = Ink, modifier = Modifier.padding(top = 2.dp))
                            }
                        }
                    }
                    if (arr.length() > 6) Text("…共 ${arr.length()} 个周期，仅显示最近 6",
                        style = MaterialTheme.typography.labelSmall, color = MutedSoft)
                }
            }
            TextButton(onClick = { openBatterySettings(ctx) }) { Text("电池优化白名单", color = Muted) }
            TextButton(onClick = { openUsageAccess(ctx) }) { Text("使用情况访问（前台感知联想用；需手动授权）", color = Muted) }
            Spacer(Modifier.height(24.dp))
        }
    }
}

/** 设置页分区卡：surface-card 色块即层级（无阴影），hairline 边 + 圆角 12（聊天页同款） */
@Composable
private fun SectionCard(title: String, content: @Composable ColumnScope.() -> Unit) {
    Surface(color = SurfaceCard, shape = RoundedCornerShape(12.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, Hairline),
            modifier = Modifier.fillMaxWidth().padding(bottom = 14.dp)) {
        Column(Modifier.padding(16.dp)) {
            Text(title, style = MaterialTheme.typography.titleSmall)
            Spacer(Modifier.height(8.dp))
            content()
        }
    }
}

@Composable
private fun CommitRow(label: String, initial: String, secret: Boolean, state: androidx.compose.runtime.MutableState<String>) {
    Column {
        Text(label, style = MaterialTheme.typography.bodySmall)
        OutlinedTextField(
            state.value, { state.value = it }, Modifier.fillMaxWidth(), singleLine = true,
            visualTransformation = if (secret) PasswordVisualTransformation()
                                   else androidx.compose.ui.text.input.VisualTransformation.None,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
        )
    }
}

@Composable
private fun SaveButton(label: String, onClick: () -> Unit) {
    Button(onClick = onClick, colors = ButtonDefaults.buttonColors(Coral),
        shape = MaterialTheme.shapes.small, modifier = Modifier.padding(top = 8.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium)
    }
}

@Composable
private fun TestButton(which: String, label: String, busy: String, onTest: (String) -> Unit) {
    OutlinedButton(
        onClick = { onTest(which) },
        enabled = busy.isEmpty(),
        shape = MaterialTheme.shapes.small,
        modifier = Modifier.padding(top = 8.dp),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = Coral),
        // 该按钮恒在奶油分区卡上 → 文字固定用深色系的珊瑚；禁用态由 alpha 表达，不再手动换色
        border = androidx.compose.foundation.BorderStroke(1.dp, if (busy.isEmpty()) Coral else Hairline)) {
        Text(if (busy == which) "测试中…" else label, style = MaterialTheme.typography.labelMedium)
    }
}

private fun restartApp(ctx: Context) {
    ctx.startActivity(Intent(ctx, MainActivity::class.java)
        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK))
    (ctx as? Activity)?.finishAndRemoveTask()
    Runtime.getRuntime().exit(0)  // chaquopy 进程内模块态必须真杀才干净
}

private fun openBatterySettings(ctx: Context) {
    try {
        ctx.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (e: Exception) {
        ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
            .setData(android.net.Uri.fromParts("package", ctx.packageName, null))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}

private fun openUsageAccess(ctx: Context) {
    try {
        ctx.startActivity(Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    } catch (e: Exception) {
        ctx.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
            .setData(android.net.Uri.fromParts("package", ctx.packageName, null))
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
    }
}
