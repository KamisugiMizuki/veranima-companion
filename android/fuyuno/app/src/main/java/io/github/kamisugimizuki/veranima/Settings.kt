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
import androidx.compose.ui.text.font.FontWeight
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
        Spacer(Modifier.height(8.dp))
        GalaxyNavRow(icon = IconUserModel, title = "用户画像（UserModel）",
            subtitle = "角色眼中的你 · 13 项可编辑 · 锁定的不被自动改写",
            onClick = { nav.navigate("usermodel_detail") })
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

// ---------- 二级页：UserModel（用户画像单一真源 data/usermodel.json） ----------

/** 13 键的展示定义：键 → 标题+描述（描述=告诉用户这格会被角色怎么用）。 */
private val UM_FIELDS = listOf(
    "real_name" to ("名字" to "你告诉过角色怎么称呼你的全名。留空则角色只用称呼账里的叫法。"),
    "nickname_pref" to ("希望被怎么称呼" to "你自己偏好的称呼倾向；具体在叫什么是各角色的称呼账（按关系演化），这里只定大方向。"),
    "gender" to ("性别" to "你的性别。角色决定自称、语气和暧昧分寸时用。"),
    "age" to ("年龄" to "你的年龄或人生阶段。影响话题参照系（角色不会拿它跟你套近乎装熟）。"),
    "occupation" to ("职业" to "你做什么工作。角色聊你的日常、体谅你忙不忙时的基本盘。"),
    "city" to ("城市" to "你住哪。异地角色算天气、说「你那边」时以这座城为准。"),
    "love_language" to ("吃哪套关心" to "言语肯定 / 实际行动 / 陪伴 / 礼物 / 服务——角色选关心方式时优先用这套。"),
    "comfort_style" to ("低落时想要什么" to "情绪差的时候希望被怎么对待：陪着、给建议、还是别烦你。角色照这个来，不猜。"),
    "teasing_tolerance" to ("被调侃接受度" to "高 / 中 / 低。决定角色敢不敢损你、损多狠。"),
    "health_notes" to ("健康注意项" to "长期要注意的事（熬夜伤胃、颈椎、过敏……）。角色会记着提醒，但不当医疗设备用。"),
    "personality_traits" to ("性格自述" to "你怎么看自己。角色据此预判你的反应，但不会拿它给你下定义。"),
    "current_goal" to ("近期在忙什么" to "手上正在推的事。角色主动关心进度、理解你没空聊的由头。"),
    "pending_events" to ("快发生的事" to "考试、出差、复查这类有日期的 pending 事项。角色会记着日子。"),
)

@Composable
fun UserModelScreen(onBack: () -> Unit) {
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var loaded by remember { mutableStateOf(false) }
    // 每键两份本地态：编辑框文本 + 锁定开关；载入时从 bridge 灌一次
    val values = remember { UM_FIELDS.associate { (k, _) -> k to mutableStateOf("") } }
    val pins = remember { UM_FIELDS.associate { (k, _) -> k to mutableStateOf(false) } }
    val srcs = remember { UM_FIELDS.associate { (k, _) -> k to mutableStateOf("") } }
    var portraits by remember { mutableStateOf(listOf<JSONObject>()) }
    LaunchedEffect(Unit) {
        runCatching {
            val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("usermodel_get").toString() })
            if (!o.optBoolean("ok")) return@runCatching
            val prof = o.getJSONObject("profile")
            for ((k, _) in UM_FIELDS) if (prof.has(k)) {
                val e = prof.getJSONObject(k)
                values[k]!!.value = e.optString("value")
                pins[k]!!.value = e.optBoolean("pinned")
                srcs[k]!!.value = e.optString("source")
            }
            val arr = o.getJSONArray("portraits")
            portraits = (0 until arr.length()).map { arr.getJSONObject(it) }
            loaded = true
        }
    }
    fun save(k: String) = scope.launch {
        val o = JSONObject(withContext(Dispatchers.IO) {
            bridge.callAttr("usermodel_set", k, values[k]!!.value,
                if (pins[k]!!.value) "1" else "0").toString()  // 显式 0/1：'' =不动锁定态，取消锁定会丢
        })
        snackbar.showSnackbar(if (o.optBoolean("ok")) "$k 已保存" else "保存失败: ${o.optString("error")}")
    }
    Scaffold(snackbarHost = { SnackbarHost(snackbar) }, containerColor = PageBg(),
        topBar = {
            Row(Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 8.dp),
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onBack) { Text("返回", color = Muted()) }
                Text("用户画像", style = MaterialTheme.typography.headlineSmall)
            }
        }) { pad ->
        Column(Modifier.padding(pad).padding(horizontal = 16.dp).verticalScroll(rememberScrollState())) {
            Text("这是角色们共同维护的「你是谁」：对话中自动提取，你也可以手改。" +
                    "打开某格的「锁定」后，自动提取不再改它（你亲口说的话仍可更新）。",
                fontSize = 12.sp, color = Muted(), modifier = Modifier.padding(bottom = 12.dp))
            if (!loaded) { Text("读取中…", color = Muted()); return@Column }
            for ((k, pair) in UM_FIELDS) {
                val (title, desc) = pair
                SectionCard(title) {
                    Text(desc, fontSize = 12.sp, color = Muted(),
                        modifier = Modifier.padding(bottom = 8.dp))
                    val src = srcs[k]!!.value
                    if (src.isNotEmpty()) Text(
                        if (src == "user") "来源：你亲口说的" else "来源：对话中提取",
                        fontSize = 11.sp, color = Muted(), modifier = Modifier.padding(bottom = 4.dp))
                    OutlinedTextField(values[k]!!.value, { values[k]!!.value = it },
                        Modifier.fillMaxWidth(), singleLine = true,
                        keyboardOptions = androidx.compose.foundation.text.KeyboardOptions(imeAction = ImeAction.Done))
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        Text("锁定不被自动改写", fontSize = 13.sp, color = PrimaryInk(),
                            modifier = Modifier.weight(1f))
                        Switch(pins[k]!!.value, { on -> pins[k]!!.value = on; save(k) },
                            colors = SwitchDefaults.colors(
                                checkedTrackColor = InvertSurface(), checkedThumbColor = OnInvert(),
                                uncheckedTrackColor = CardBg(), uncheckedThumbColor = Muted(),
                                uncheckedBorderColor = CardBorder()))
                    }
                    SaveButton("保存") { save(k) }
                }
            }
            SectionCard("我眼中的你（角色写，只读）") {
                Text("每个角色在夜间整理时写下自己对你的印象——同一份画像，不同人读出不同的你。" +
                        "这里改不了：那是她们的判断。", fontSize = 12.sp, color = Muted(),
                    modifier = Modifier.padding(bottom = 8.dp))
                if (portraits.isEmpty()) Text("还没有角色写过（需要聊出足够素材后夜里生成）。",
                    fontSize = 13.sp, color = Muted())
                portraits.forEach { p ->
                    Text(p.optString("name"), fontSize = 13.sp, fontWeight = FontWeight(600),
                        color = PrimaryInk(), modifier = Modifier.padding(top = 6.dp))
                    Text(p.optString("text"), fontSize = 13.sp, color = PrimaryInk(),
                        modifier = Modifier.padding(top = 2.dp))
                }
            }
            Spacer(Modifier.height(24.dp))
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
