package io.github.kamisugimizuki.veranima

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.DateRange
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
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

/**
 * 顶层导航壳（MOMENTS_MULTIROLE_SPEC P1）：底栏三 tab（聊天/好友动态/设置）。
 * 聊天 tab=角色列表页（顶栏右端＋导入）→ 点入单角色会话页（ChatPage，
 * 顶栏右端齿轮→角色私产页 RoleSpace）；动态页 P1=占位；设置=全局项。
 * 两级顶栏图标语义分离：＋=往通讯录加人（全局），齿轮=该角色私产。
 */

/** 三级界面栈：MainShell（tab 层）→ 会话页 → 角色私产页（全屏，tab 隐藏）。 */
@Composable
fun AppRoot() {
    // 打开的角色会话（null=停在列表层）；私产页从会话页推入
    val openRole = remember { mutableStateOf<String?>(null) }
    val spaceRole = remember { mutableStateOf<String?>(null) }
    val r = openRole.value
    val sp = spaceRole.value
    when {
        sp != null -> RoleSpaceScreen(role = sp, onBack = { spaceRole.value = null })
        r != null -> ChatScreen(role = r, onBack = { openRole.value = null },
            onOpenSpace = { spaceRole.value = r })
        else -> MainShell(onOpenRole = { openRole.value = it })
    }
}

@Composable
fun MainShell(onOpenRole: (String) -> Unit) {
    val nav = rememberNavController()
    val tab = remember { mutableStateOf("chat") }
    Scaffold(
        containerColor = PageBg(),
        bottomBar = { GalaxyBottomBar(tab.value) { tab.value = it } },
    ) { pad ->
        Box(Modifier.padding(pad)) {
            when (tab.value) {
                "chat" -> RoleListScreen(onOpenRole)
                "feed" -> MomentsPlaceholder()
                else -> SettingsTab()
            }
        }
    }
}

/** 底栏：三枚 icon+字导航，选中=黑底白字反色胶囊（Galaxy 黑白反色语言）。 */
@Composable
private fun GalaxyBottomBar(current: String, onSelect: (String) -> Unit) {
    Row(
        Modifier.fillMaxWidth().background(CardBg())
            .border(1.dp, CardBorder())
            .padding(vertical = 8.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
    ) {
        listOf("chat" to "聊天" to Icons.Filled.Edit,
               "feed" to "好友动态" to Icons.Filled.DateRange,
               "settings" to "设置" to Icons.Filled.Settings).forEach { (pair, icon) ->
            val (id, label) = pair
            val sel = current == id
            Column(horizontalAlignment = Alignment.CenterHorizontally,
                modifier = Modifier
                    .clip(RoundedCornerShape(12.dp))
                    .background(if (sel) InvertSurface() else Color.Transparent)
                    .clickable { onSelect(id) }
                    .padding(horizontal = 18.dp, vertical = 6.dp)) {
                Icon(icon, contentDescription = label,
                    tint = if (sel) OnInvert() else Muted(), modifier = Modifier.size(22.dp))
                Spacer(Modifier.height(2.dp))
                Text(label, fontSize = 10.sp,
                    color = if (sel) OnInvert() else Muted(),
                    fontWeight = if (sel) FontWeight.Medium else FontWeight.Normal)
            }
        }
    }
}

// ---------- 聊天 tab：角色列表 ----------

data class RoleRow(val id: String, val name: String, val preview: String,
                   val time: String, val unread: Int, val avatar: String, val active: Boolean)

@Composable
fun RoleListScreen(onOpenRole: (String) -> Unit) {
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var rows by remember { mutableStateOf<List<RoleRow>?>(null) }
    var importing by remember { mutableStateOf(false) }
    var ready by remember { mutableStateOf(false) }
    fun reload() = scope.launch {
        // boot 异步进行中的窗口期 roles_list 会失败 → 0.8s 自旋重试（ready 后置真停旋）
        repeat(40) {
            val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("roles_list").toString() })
            if (o.optBoolean("ok")) {
                val arr = o.optJSONArray("roles") ?: org.json.JSONArray()
                rows = (0 until arr.length()).map { i ->
                    val r = arr.getJSONObject(i)
                    RoleRow(r.getString("id"), r.getString("name"), r.optString("preview"),
                            r.optString("time"), r.optInt("unread"), r.optString("avatar"),
                            r.optBoolean("active"))
                }
                ready = true
                return@launch
            }
            kotlinx.coroutines.delay(800)
        }
    }
    LaunchedEffect(Unit) { reload() }
    LaunchedEffect(ready) { if (!ready && rows == null) reload() }
    // 导入角色包（.char）→ inbox/pending.char → role_import（裁决 UI-2：入口在本页顶栏＋）
    val importRole = rememberLauncherForActivityResult(ActivityResultContracts.GetContent()) { uri ->
        if (uri != null) scope.launch {
            importing = true
            runCatching {
                withContext(Dispatchers.IO) {
                    val dst = java.io.File(ctx.filesDir, "inbox"); dst.mkdirs()
                    ctx.contentResolver.openInputStream(uri)?.use { inp ->
                        dst.resolve("pending.char").outputStream().use { out -> inp.copyTo(out) }
                    }
                    bridge.callAttr("role_import").toString()
                }
            }
            importing = false
            reload()
        }
    }
    Column(Modifier.fillMaxSize().background(PageBg()).statusBarsPadding()) {
        // 顶栏：应用名居中｜右端＝加号导入
        Box(Modifier.fillMaxWidth().padding(vertical = 10.dp)) {
            Text("Veranima", fontSize = 20.sp, fontWeight = FontWeight.SemiBold,
                color = PrimaryInk(), modifier = Modifier.align(Alignment.Center))
            IconButton(onClick = { importRole.launch("application/zip") },
                modifier = Modifier.align(Alignment.CenterEnd).padding(end = 6.dp)) {
                Icon(Icons.Filled.Add, contentDescription = "导入新角色", tint = PrimaryInk())
            }
        }
        if (importing) Text("导入中…", fontSize = 12.sp, color = Muted(),
            modifier = Modifier.padding(horizontal = 20.dp))
        val list = rows
        if (list == null) {
            Text("加载中…", color = Muted(), modifier = Modifier.padding(20.dp))
        } else {
            LazyColumn(Modifier.fillMaxSize()) {
                items(list, key = { it.id }) { r ->
                    RoleListItem(r, onClick = { onOpenRole(r.id) })
                }
            }
        }
    }
}

/** 圆角正方形头像（裁决 UI-3）：用户提供 characters/<id>/portrait.jpg；缺=首字母几何块。 */
@Composable
fun RoleAvatar(name: String, avatarPath: String, size: Int = 52) {
    val shape = RoundedCornerShape(12.dp)
    Box(Modifier.size(size.dp).clip(shape).border(1.dp, CardBorder(), shape)
        .background(PageBg())) {
        if (avatarPath.isNotEmpty()) {
            coil.compose.AsyncImage(model = java.io.File(avatarPath), contentDescription = name,
                contentScale = androidx.compose.ui.layout.ContentScale.Crop,
                modifier = Modifier.fillMaxSize())
        } else {
            Box(Modifier.fillMaxSize().background(InvertSurface()),
                contentAlignment = Alignment.Center) {
                Text(name.take(1), color = OnInvert(), fontSize = (size * 0.42f).sp,
                    fontWeight = FontWeight.Bold)
            }
        }
    }
}

@Composable
private fun RoleListItem(r: RoleRow, onClick: () -> Unit) {
    Row(Modifier.fillMaxWidth().clickable(onClick = onClick)
            .padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically) {
        RoleAvatar(r.name, r.avatar)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(r.name, fontSize = 16.sp, fontWeight = FontWeight.Medium, color = PrimaryInk())
                if (r.active) {
                    Spacer(Modifier.width(6.dp))
                    Text("活跃", fontSize = 10.sp, color = AccentBlue,
                        modifier = Modifier.background(AccentBlue.copy(alpha = 0.14f),
                            RoundedCornerShape(4.dp)).padding(horizontal = 4.dp, vertical = 1.dp))
                }
            }
            Spacer(Modifier.height(2.dp))
            Text(r.preview.ifEmpty { "还没有对话" }, fontSize = 13.sp, color = Muted(),
                maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        Spacer(Modifier.width(8.dp))
        Column(horizontalAlignment = Alignment.End) {
            Text(shortTime(r.time), fontSize = 11.sp, color = MutedSoft())
            Spacer(Modifier.height(4.dp))
            if (r.unread > 0) {
                Box(Modifier.clip(CircleShape).background(InvertSurface())
                    .widthIn(min = 18.dp).height(18.dp), contentAlignment = Alignment.Center) {
                    Text(if (r.unread > 99) "99+" else r.unread.toString(),
                        fontSize = 10.sp, color = OnInvert(),
                        modifier = Modifier.padding(horizontal = 5.dp))
                }
            } else {
                Spacer(Modifier.height(18.dp))
            }
        }
    }
    HorizontalDivider(color = Hairline(), thickness = 0.5.dp,
        modifier = Modifier.padding(start = 80.dp))
}

private fun shortTime(iso: String): String = try {
    if (iso.isEmpty()) "" else {
        val t = java.time.OffsetDateTime.parse(iso).atZoneSameInstant(java.time.ZoneId.systemDefault())
        val d = t.toLocalDate(); val now = java.time.LocalDate.now()
        when {
            d == now -> t.toLocalTime().format(java.time.format.DateTimeFormatter.ofPattern("HH:mm"))
            d == now.minusDays(1) -> "昨天"
            else -> t.format(java.time.format.DateTimeFormatter.ofPattern("MM-dd"))
        }
    }
} catch (e: Exception) { "" }

// ---------- 好友动态 tab（P2 接 moments 引擎，P1 诚实占位） ----------

@Composable
private fun MomentsPlaceholder() {
    Column(Modifier.fillMaxSize().background(PageBg()).statusBarsPadding()
            .padding(20.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Text("好友动态", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = PrimaryInk(),
            modifier = Modifier.fillMaxWidth())
        Spacer(Modifier.height(80.dp))
        Box(Modifier.size(72.dp).border(1.dp, CardBorder(), CircleShape),
            contentAlignment = Alignment.Center) {
            Icon(Icons.Filled.DateRange, contentDescription = null, tint = Muted(),
                modifier = Modifier.size(30.dp))
        }
        Spacer(Modifier.height(16.dp))
        Text("她们还没发过动态", fontSize = 14.sp, color = Muted())
        Text("动态来自角色的虚拟生活——日程、心情、碎碎念，攒够了自然会有",
            fontSize = 12.sp, color = MutedSoft())
    }
}

// ---------- 设置 tab（内嵌 NavHost；UI-4 收纳：主页纯一列，API 表单进二级页） ----------

@Composable
fun SettingsTab() {
    val nav = rememberNavController()
    val atRoot = nav.currentBackStackEntryAsState()?.value?.destination?.route
        .let { it == null || it == "settings_main" }
    androidx.activity.compose.BackHandler(enabled = !atRoot) { nav.popBackStack() }
    NavHost(navController = nav, startDestination = "settings_main",
        modifier = Modifier.fillMaxSize()) {
        composable("settings_main") { SettingsMainScreen(nav) }
        composable("api_detail") { ApiDetailScreen(onBack = { nav.popBackStack() }) }
        composable("active_role") { ActiveRoleScreen(onBack = { nav.popBackStack() }) }
        composable("backup_detail") { BackupDetailScreen(onBack = { nav.popBackStack() }) }
        composable("memory_detail") { MemoryDetailScreen(onBack = { nav.popBackStack() }) }
        composable("sleep_detail") { SleepDetailScreen(onBack = { nav.popBackStack() }) }
    }
}

// ---------- 角色私产页（聊天顶栏齿轮进入；裁决 UI-1） ----------

@Composable
fun RoleSpaceScreen(role: String, onBack: () -> Unit) {
    // 页面内二级：bond/sleep 子页直接嵌在本壳（避开外层 tab 干扰）
    val sub = remember { mutableStateOf<String?>(null) }
    when (sub.value) {
        "bond" -> { RelationshipDetailScreen(onBack = { sub.value = null }, role = role); return }
    }
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val snackbar = remember { SnackbarHostState() }
    var name by remember { mutableStateOf(role) }
    var avatar by remember { mutableStateOf("") }
    var rhythm by remember { mutableStateOf<JSONObject?>(null) }
    var confirm by remember { mutableStateOf<String?>(null) }   // "intimacy"/"messages"
    LaunchedEffect(role) {
        name = withContext(Dispatchers.IO) { bridge.callAttr("role_label", role).toString() }
        val rr = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("rhythm_status", role).toString() })
        rhythm = rr.optJSONObject("role_rhythm")
        val rl = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("roles_list").toString() })
        rl.optJSONArray("roles")?.let { a ->
            for (i in 0 until a.length()) {
                val o = a.getJSONObject(i)
                if (o.optString("id") == role) avatar = o.optString("avatar")
            }
        }
    }
    Scaffold(snackbarHost = { SnackbarHost(snackbar) }, containerColor = PageBg(),
        topBar = {
            Row(Modifier.fillMaxWidth().statusBarsPadding().padding(horizontal = 4.dp),
                verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onBack) { Text("返回", color = Muted()) }
                Text("角色私产", fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = PrimaryInk())
            }
        }) { pad ->
        Column(Modifier.padding(pad).padding(horizontal = 16.dp).verticalScroll(rememberScrollState())) {
            // 头部：方头像+名字
            Row(verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier.padding(vertical = 14.dp)) {
                RoleAvatar(name, avatar, size = 56)
                Spacer(Modifier.width(14.dp))
                Column {
                    Text(name, fontSize = 18.sp, fontWeight = FontWeight.SemiBold, color = PrimaryInk())
                    Text("以上设置仅影响${name}，不波及其他角色", fontSize = 11.sp, color = MutedSoft())
                }
            }
            Spacer(Modifier.height(8.dp))
            GalaxyNavRow(icon = IconBond, title = "羁绊图谱",
                subtitle = "亲密度 / 信任 / 理解 · TA 与你的关系账",
                onClick = { sub.value = "bond" })
            Spacer(Modifier.height(8.dp))
            // 角色当前作息卡（自用户睡眠报告页迁入，裁决 UI-1）
            RoleRhythmCard(rhythm, name)
            Spacer(Modifier.height(8.dp))
            GalaxyNavRow(icon = IconMoon, title = "导出此角色",
                subtitle = "轻量 .char 包（不含立绘语音）",
                onClick = {
                    scope.launch {
                        val o = JSONObject(withContext(Dispatchers.IO) {
                            bridge.callAttr("role_export", role).toString()
                        })
                        if (!o.optBoolean("ok")) { snackbar.showSnackbar("导出失败: ${o.optString("error")}"); return@launch }
                        val src = java.io.File(ctx.filesDir, "inbox/role_pending.char")
                        val mime = androidx.activity.result.contract.ActivityResultContracts.CreateDocument("application/zip")
                        // 简化：直接写 Downloads（SAF 目标选择留给 P3 打磨）
                        val dst = java.io.File(android.os.Environment.getExternalStoragePublicDirectory(
                            android.os.Environment.DIRECTORY_DOWNLOADS), "veranima-role-$role.char")
                        runCatching { src.copyTo(dst, overwrite = true) }
                            .onSuccess { snackbar.showSnackbar("已导出到下载目录 ${dst.name}") }
                            .onFailure { snackbar.showSnackbar("拷贝失败: ${it.message}") }
                    }
                })
            Spacer(Modifier.height(8.dp))
            GalaxyNavRow(icon = IconReset, title = "重置关系与作息",
                subtitle = "七维/依恋/作息回到卡面初始值（共享记忆不动）",
                onClick = { confirm = "intimacy" })
            Spacer(Modifier.height(8.dp))
            GalaxyNavRow(icon = IconTrash, title = "清空会话历史",
                subtitle = "只删你和${name}的聊天记录（不可恢复）",
                onClick = { confirm = "messages" })
            Spacer(Modifier.height(24.dp))
        }
    }
    confirm?.let { mode ->
        AlertDialog(onDismissRequest = { confirm = null },
            confirmButton = {
                TextButton(onClick = {
                    confirm = null
                    scope.launch {
                        val o = JSONObject(withContext(Dispatchers.IO) {
                            bridge.callAttr("role_reset", role, mode).toString()
                        })
                        snackbar.showSnackbar(if (o.optBoolean("ok")) "已完成" else "失败: ${o.optString("error")}")
                        if (mode == "intimacy") {
                            rhythm = JSONObject(withContext(Dispatchers.IO) {
                                bridge.callAttr("rhythm_status", role).toString()
                            }).optJSONObject("role_rhythm")
                        }
                    }
                }) { Text("确认", color = PrimaryInk()) }
            },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("取消", color = Muted()) } },
            title = { Text(if (mode == "intimacy") "重置与${name}的关系？" else "清空${name}的会话历史？",
                color = PrimaryInk()) },
            text = { Text(if (mode == "intimacy") "羁绊回到初见、作息偏移归零；你们共同的记忆库不受影响。"
                          else "这段聊天记录将永久删除。", color = Muted()) },
            containerColor = CardBg())
    }
}

/** 角色作息卡（从 SleepDetailScreen 整体迁入；数据源 rhythm_status(role)）。 */
@Composable
fun RoleRhythmCard(rr: JSONObject?, name: String) {
    val d = rr ?: return
    if (d.length() == 0) return
    val napping = d.optBoolean("is_napping")
    val offset = d.optInt("offset_minutes", 0)
    val maxOff = d.optInt("max_offset_minutes", 720)
    GalaxyCard(modifier = Modifier.fillMaxWidth()) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("${d.optString("name", name)}的当前作息", fontSize = 14.sp,
                fontWeight = FontWeight.Medium, color = PrimaryInk(),
                modifier = Modifier.weight(1f))
            GalaxyTag(if (napping) "睡梦中" else "清醒",
                if (napping) AccentBlue else AccentSage)
        }
        Spacer(Modifier.height(10.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceEvenly) {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(d.optString("wake_at", "—").ifEmpty { "—" },
                    fontSize = 24.sp, fontWeight = FontWeight.Bold, color = PrimaryInk())
                Text("起床", fontSize = 11.sp, color = Muted())
            }
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(d.optString("sleep_at", "—").ifEmpty { "—" },
                    fontSize = 24.sp, fontWeight = FontWeight.Bold, color = PrimaryInk())
                Text("就寝", fontSize = 11.sp, color = Muted())
            }
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(if (offset == 0) "基准" else "${if (offset > 0) "+" else ""}${offset}分",
                    fontSize = 24.sp, fontWeight = FontWeight.Bold,
                    color = if (offset == 0) PrimaryInk() else AccentTaupe)
                Text(if (maxOff < 720) "作息偏移·上限±${maxOff / 60}h" else "作息偏移",
                    fontSize = 11.sp, color = Muted())
            }
        }
        val place = d.optString("now_place")
        val activity = d.optString("now_activity")
        if (!napping && (place.isNotEmpty() || activity.isNotEmpty())) {
            Spacer(Modifier.height(10.dp))
            Text("此刻（${d.optString("local_time", "")}）" +
                    listOfNotNull(
                        activity.takeIf { it.isNotEmpty() }?.let { roleActLabel(it) },
                        place.takeIf { it.isNotEmpty() })
                        .joinToString(" · "),
                fontSize = 12.sp, color = Muted())
        }
    }
}

private fun roleActLabel(key: String): String = mapOf(
    "wake_routine" to "晨间梳洗", "focused_practice" to "在专注做事", "reset" to "在路上",
    "personal_interest_a" to "在自己的爱好里", "personal_interest_b" to "在自己的爱好里",
    "quiet_rest" to "歇着", "sleep" to "睡眠中", "gap" to "在发呆间隙",
    "commute_transit" to "在通勤路上", "model_training_work" to "在跑训练",
    "late_takeout_dinner" to "在吃夜宵外卖", "meme_archiving" to "在收藏表情包",
    "video_with_you" to "在等你同步放映", "blog_browsing" to "在刷博客",
).getOrDefault(key, key)

private val IconReset: ImageVector by lazy { Icons.Filled.Refresh }
private val IconTrash: ImageVector by lazy { Icons.Filled.Delete }
