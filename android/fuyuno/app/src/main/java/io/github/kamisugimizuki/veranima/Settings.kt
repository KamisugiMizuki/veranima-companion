package io.github.kamisugimizuki.veranima

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.navigation.NavHostController
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.chaquo.python.Python
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/** 设置页：表单直连 bridge（写 config.yaml，改动后点右上"重启生效"）。
 *  导入/导出走 SAF 系统文件选择器（GetContent/OpenDocument），不再依赖 inbox 约定。
 *
 *  2026-09-01 UI 重构：记忆库/羁绊/睡眠三块纯文本拆为 Galaxy 独立详情页
 *  （NavController 路由 memory_detail / relationship_detail / sleep_detail）。 */
@Composable
fun SettingsScreen(onBack: () -> Unit) {
    val nav = rememberNavController()
    // 硬件返回键：详情页内=回设置主面（状态保留），主面=退出设置（原逻辑不变）
    val atRoot = nav.currentBackStackEntryAsState()?.value?.destination?.route
        .let { it == null || it == "settings_main" }
    androidx.activity.compose.BackHandler(enabled = !atRoot) { nav.popBackStack() }
    NavHost(navController = nav, startDestination = "settings_main") {
        composable("settings_main") { SettingsMainScreen(onBack, nav) }
        composable("memory_detail") { MemoryDetailScreen(onBack = { nav.popBackStack() }) }
        composable("relationship_detail") { RelationshipDetailScreen(onBack = { nav.popBackStack() }) }
        composable("sleep_detail") { SleepDetailScreen(onBack = { nav.popBackStack() }) }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsMainScreen(onBack: () -> Unit, nav: androidx.navigation.NavHostController) {
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
             containerColor = PageBg(),
             topBar = {
        // 聊天页风格统一（2026-08-29）：衬线页头、无灰底；珊瑚只留给「重启生效」
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = onBack) { Text("返回", color = Muted()) }
            Text("设置", style = MaterialTheme.typography.headlineSmall)
            Spacer(Modifier.weight(1f))
            if (dirty) Button(onClick = { restartApp(ctx) },
                colors = ButtonDefaults.buttonColors(InvertSurface(), OnInvert()),
                shape = MaterialTheme.shapes.small) { Text("重启生效") }
        }
    }) { pad ->
        Column(Modifier.padding(pad).padding(horizontal = 16.dp).verticalScroll(rememberScrollState())) {
            val st = s
            if (st == null) { Text("读取设置中…", color = Muted()); return@Column }
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
                        TextButton(onClick = { pendingRole = c; exportRole.launch("veranima-role-$c.char") }) { Text("导出…", color = Muted()) }
                    }
                }
                TextButton(onClick = { importRole.launch("*/*") }) { Text("导入角色包（.char）", color = Muted()) }
            }

            SectionCard("共享记忆备份（导出=全部角色的共同记忆 zip；导入=全量覆盖）") {
                Row {
                    Button(onClick = { exportBackup.launch("veranima-backup.zip") },
                        colors = ButtonDefaults.buttonColors(InvertSurface(), OnInvert()), shape = MaterialTheme.shapes.small) { Text("导出…") }
                    Spacer(Modifier.width(12.dp))
                    Button(onClick = { importBackup.launch("application/zip") },
                        colors = ButtonDefaults.buttonColors(InvertSurface(), OnInvert()), shape = MaterialTheme.shapes.small) { Text("导入…") }
                }
            }

            // ---- 2026-09-01 UI 重构：三块纯文本数据拆分 Galaxy 独立详情页 ----
            Spacer(Modifier.height(2.dp))
            Text("数据视图", fontSize = 13.sp, fontWeight = androidx.compose.ui.text.font.FontWeight.SemiBold,
                color = MutedSoft(), modifier = Modifier.padding(bottom = 8.dp))
            GalaxyNavRow(icon = IconBond, title = "羁绊图谱", subtitle = "亲密度 / 信任 / 理解 · 三环与趋势",
                onClick = { nav.navigate("relationship_detail") })
            Spacer(Modifier.height(8.dp))
            GalaxyNavRow(icon = IconMemoryVault, title = "记忆库", subtitle = "向量记忆总览 · 密度分布 · 时间轴",
                onClick = { nav.navigate("memory_detail") })
            Spacer(Modifier.height(8.dp))
            GalaxyNavRow(icon = IconMoon, title = "睡眠报告", subtitle = "实时状态 · 时长 · 作息分布",
                onClick = { nav.navigate("sleep_detail") })
            Spacer(Modifier.height(14.dp))
            TextButton(onClick = { openBatterySettings(ctx) }) { Text("电池优化白名单", color = Muted()) }
            TextButton(onClick = { openUsageAccess(ctx) }) { Text("使用情况访问（前台感知联想用；需手动授权）", color = Muted()) }
            Spacer(Modifier.height(24.dp))
        }
    }
}

/** 设置页分区卡：Galaxy 规范——白卡 + 黑色细边框 1dp + 圆角 12（夜间自动反色） */
@Composable
private fun SectionCard(title: String, content: @Composable ColumnScope.() -> Unit) {
    Surface(color = CardBg(), shape = RoundedCornerShape(12.dp),
            border = androidx.compose.foundation.BorderStroke(1.dp, CardBorder()),
            modifier = Modifier.fillMaxWidth().padding(bottom = 14.dp)) {
        Column(Modifier.padding(16.dp)) {
            Text(title, style = MaterialTheme.typography.titleSmall, color = PrimaryInk())
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
    Button(onClick = onClick, colors = ButtonDefaults.buttonColors(InvertSurface(), OnInvert()),
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
        colors = ButtonDefaults.outlinedButtonColors(contentColor = PrimaryInk()),
        // 该按钮恒在奶油分区卡上 → 文字固定用深色系的珊瑚；禁用态由 alpha 表达，不再手动换色
        border = androidx.compose.foundation.BorderStroke(1.dp, if (busy.isEmpty()) PrimaryInk() else Hairline())) {
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
