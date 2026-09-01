package io.github.kamisugimizuki.veranima

import android.app.Activity
import android.content.Context
import android.content.Intent
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.Face
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Settings
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
import com.chaquo.python.Python
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

/**
 * 设置 tab（2026-09-01 UI-4 裁决）：主页=纯一列 GalaxyNavRow 零表单，
 * 大块内容收纳进二级页：API 配置 / 当前活跃角色 / 共享记忆备份 /
 * 共同记忆库 / 用户睡眠报告；系统权限两项行内直跳。行副标带当前值摘要。
 * 角色私产（羁绊/作息/导出/重置）不在这里——在聊天顶栏齿轮的角色私产页。
 */

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsMainScreen(nav: NavHostController) {
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val ctx = LocalContext.current
    var f by remember { mutableStateOf<JSONObject?>(null) }
    var activeChar by remember { mutableStateOf("") }
    LaunchedEffect(Unit) {
        runCatching {
            val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("get_settings").toString() })
            if (o.optBoolean("ok")) {
                f = o.getJSONObject("fields")
                activeChar = o.getString("active_character")
            }
        }
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
        Spacer(Modifier.height(8.dp))
        val ff = f
        GalaxyNavRow(icon = Icons.Filled.Build, title = "API 配置",
            subtitle = ff?.let { "LLM ${it.getString("llm_model")}" } ?: "语言模型 / Embedding / 搜索",
            onClick = { nav.navigate("api_detail") })
        Spacer(Modifier.height(8.dp))
        GalaxyNavRow(icon = Icons.Filled.Face, title = "当前活跃角色",
            subtitle = "对外应答与主动消息的默认卡：$activeChar",
            onClick = { nav.navigate("active_role") })
        Spacer(Modifier.height(8.dp))
        GalaxyNavRow(icon = Icons.Filled.Info, title = "共享记忆备份",
            subtitle = "导出/导入全角色共同记忆 zip",
            onClick = { nav.navigate("backup_detail") })
        Spacer(Modifier.height(8.dp))
        GalaxyNavRow(icon = IconMemoryVault, title = "共同记忆库",
            subtitle = "向量记忆总览 · 密度分布 · 时间轴",
            onClick = { nav.navigate("memory_detail") })
        Spacer(Modifier.height(8.dp))
        GalaxyNavRow(icon = IconMoon, title = "用户睡眠报告",
            subtitle = "你的作息 · 实时状态 · 时长分布",
            onClick = { nav.navigate("sleep_detail") })
        Spacer(Modifier.height(14.dp))
        TextButton(onClick = { openBatterySettings(ctx) }) { Text("电池优化白名单", color = Muted()) }
        TextButton(onClick = { openUsageAccess(ctx) }) { Text("使用情况访问（前台感知联想用；需手动授权）", color = Muted()) }
        Spacer(Modifier.height(24.dp))
    }
}

// ---------- 二级页：API 配置（三组表单整体收纳） ----------

@Composable
fun ApiDetailScreen(onBack: () -> Unit) {
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val ctx = LocalContext.current
    val snackbar = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()
    var s by remember { mutableStateOf<JSONObject?>(null) }
    var dirty by remember { mutableStateOf(false) }
    suspend fun reload() {
        val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("get_settings").toString() })
        if (o.optBoolean("ok")) s = o
    }
    LaunchedEffect(Unit) { reload() }
    fun report(r: JSONObject, name: String) {
        scope.launch { snackbar.showSnackbar(if (r.optBoolean("ok")) "$name ✓" else "$name 失败: ${r.optString("error")}") }
        if (r.optBoolean("restart_required")) dirty = true
    }
    fun set(key: String, value: String) = scope.launch {
        report(JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("set_setting", key, value).toString() }), key)
    }
    val busy = remember { mutableStateOf("") }
    fun testConn(which: String) = scope.launch {
        busy.value = which
        val o = try {
            JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("test_conn", which).toString() })
        } catch (e: Exception) { JSONObject().put("ok", false).put("error", e.message ?: "bridge 异常") }
        busy.value = ""
        report(o, which)
    }
    Scaffold(snackbarHost = { SnackbarHost(snackbar) }, containerColor = PageBg(),
        topBar = {
            Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onBack) { Text("返回", color = Muted()) }
                Text("API 配置", style = MaterialTheme.typography.headlineSmall)
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
                    set("llm_api_key", k1.value); set("llm_base_url", k2.value)
                    set("llm_model", k3.value); set("llm_vision_model", k4.value)
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
                    set("embedding_api_key", k1.value); set("embedding_base_url", k2.value); set("embedding_model", k3.value)
                }
                TestButton("embedding", "测 Embedding", busy.value) { testConn(it) }
            }
            SectionCard("联网搜索（博查 Bocha，安卓唯一后端）") {
                val k1 = remember { mutableStateOf("") }
                val k2 = remember { mutableStateOf(f.getString("search_base_url")) }
                CommitRow("API Key（当前 ${f.getString("search_api_key")}；留空保存=保持不变）", "", true, k1)
                CommitRow("Base URL", f.getString("search_base_url"), false, k2)
                SaveButton("保存搜索配置") { set("search_api_key", k1.value); set("search_base_url", k2.value) }
                TestButton("search", "测搜索", busy.value) { testConn(it) }
            }
            Spacer(Modifier.height(24.dp))
        }
    }
}

// ---------- 二级页：当前活跃角色（单选） ----------

@Composable
fun ActiveRoleScreen(onBack: () -> Unit) {
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var activeChar by remember { mutableStateOf("") }
    var chars by remember { mutableStateOf(listOf<String>()) }
    var dirty by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("get_settings").toString() })
        if (o.optBoolean("ok")) {
            activeChar = o.getString("active_character")
            chars = o.getJSONArray("characters").let { a -> (0 until a.length()).map { a.getString(it) } }
        }
    }
    Scaffold(snackbarHost = { SnackbarHost(snackbar) }, containerColor = PageBg(),
        topBar = {
            Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onBack) { Text("返回", color = Muted()) }
                Text("当前活跃角色", style = MaterialTheme.typography.headlineSmall)
                Spacer(Modifier.weight(1f))
                if (dirty) Button(onClick = { restartApp(ctx) },
                    colors = ButtonDefaults.buttonColors(InvertSurface(), OnInvert()),
                    shape = MaterialTheme.shapes.small) { Text("重启生效") }
            }
        }) { pad ->
        Column(Modifier.padding(pad).padding(horizontal = 16.dp)) {
            Text("活跃角色=主动消息与 tick 的驱动对象；其他角色打开会话即聊，互不干扰。",
                fontSize = 12.sp, color = Muted(), modifier = Modifier.padding(bottom = 10.dp))
            chars.forEach { c ->
                Row(verticalAlignment = Alignment.CenterVertically,
                    modifier = Modifier.fillMaxWidth().clickable {
                        activeChar = c; dirty = true
                        scope.launch {
                            withContext(Dispatchers.IO) { bridge.callAttr("set_setting", "active_character", c) }
                        }
                    }.padding(vertical = 10.dp)) {
                    RadioButton(selected = activeChar == c, onClick = {
                        activeChar = c; dirty = true
                        scope.launch {
                            withContext(Dispatchers.IO) { bridge.callAttr("set_setting", "active_character", c) }
                        }
                    })
                    Text(c, Modifier.padding(start = 6.dp), color = PrimaryInk())
                }
                HorizontalDivider(color = Hairline(), thickness = 0.5.dp)
            }
        }
    }
}

// ---------- 二级页：共享记忆备份 ----------

@Composable
fun BackupDetailScreen(onBack: () -> Unit) {
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    val cr = ctx.contentResolver
    val exportBackup = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { uri ->
        if (uri != null) scope.launch {
            val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("backup_export").toString() })
            if (!o.optBoolean("ok")) { snackbar.showSnackbar("备份生成失败: ${o.optString("error")}"); return@launch }
            val src = java.io.File(ctx.filesDir, "inbox/backup_out.zip")
            cr.openOutputStream(uri)?.use { out -> src.inputStream().use { inp -> inp.copyTo(out) } }
            snackbar.showSnackbar("记忆备份已导出 ${src.length() / 1024}KB")
        }
    }
    val importBackup = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) scope.launch {
            val dst = java.io.File(ctx.filesDir, "inbox"); dst.mkdirs()
            cr.openInputStream(uri)?.use { inp -> dst.resolve("backup.zip").outputStream().use { out -> inp.copyTo(out) } }
            val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("backup_import").toString() })
            snackbar.showSnackbar(if (o.optBoolean("ok")) "导入完成" else "导入失败: ${o.optString("error")}")
        }
    }
    Scaffold(snackbarHost = { SnackbarHost(snackbar) }, containerColor = PageBg(),
        topBar = {
            Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onBack) { Text("返回", color = Muted()) }
                Text("共享记忆备份", style = MaterialTheme.typography.headlineSmall)
            }
        }) { pad ->
        Column(Modifier.padding(pad).padding(horizontal = 16.dp)) {
            Text("导出=全部角色的共同记忆 zip；导入=全量覆盖（跨 Windows/安卓，向量按当时模型自动重铸）。",
                fontSize = 12.sp, color = Muted(), modifier = Modifier.padding(bottom = 14.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(onClick = { exportBackup.launch("veranima-backup.zip") },
                    colors = ButtonDefaults.buttonColors(InvertSurface(), OnInvert()),
                    shape = MaterialTheme.shapes.small) { Text("导出…") }
                Button(onClick = { importBackup.launch("application/zip") },
                    colors = ButtonDefaults.buttonColors(InvertSurface(), OnInvert()),
                    shape = MaterialTheme.shapes.small) { Text("导入…") }
            }
        }
    }
}

// ---------- 共用小件 ----------

/** 设置页分区卡：Galaxy 规范——白卡 + 黑色细边框 1dp + 圆角 12（夜间自动反色） */
@Composable
internal fun SectionCard(title: String, content: @Composable ColumnScope.() -> Unit) {
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
internal fun CommitRow(label: String, initial: String, secret: Boolean, state: androidx.compose.runtime.MutableState<String>) {
    Column {
        Text(label, style = MaterialTheme.typography.bodySmall)
        OutlinedTextField(
            state.value, { state.value = it }, Modifier.fillMaxWidth(), singleLine = true,
            visualTransformation = if (secret) PasswordVisualTransformation()
                                   else androidx.compose.ui.text.input.VisualTransformation.None,
            keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(imeAction = ImeAction.Done),
        )
    }
}

@Composable
internal fun SaveButton(label: String, onClick: () -> Unit) {
    Button(onClick = onClick, colors = ButtonDefaults.buttonColors(InvertSurface(), OnInvert()),
        shape = MaterialTheme.shapes.small, modifier = Modifier.padding(top = 8.dp)) {
        Text(label, style = MaterialTheme.typography.labelMedium)
    }
}

@Composable
internal fun TestButton(which: String, label: String, busy: String, onTest: (String) -> Unit) {
    OutlinedButton(
        onClick = { onTest(which) },
        enabled = busy.isEmpty(),
        shape = MaterialTheme.shapes.small,
        modifier = Modifier.padding(top = 8.dp),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = PrimaryInk()),
        border = androidx.compose.foundation.BorderStroke(1.dp, if (busy.isEmpty()) PrimaryInk() else Hairline())) {
        Text(if (busy == which) "测试中…" else label, style = MaterialTheme.typography.labelMedium)
    }
}

internal fun restartApp(ctx: Context) {
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
