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
                "feed" -> MomentsFeed()
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

/** 圆角正方形头像（裁决 UI-3）：用户提供 characters/<id>/portrait.jpg；缺=首字母几何块。
 *  真图无边框（2026-09-01 用户裁决）；回退块保留黑底白字。 */
@Composable
fun RoleAvatar(name: String, avatarPath: String, size: Int = 52) {
    val shape = RoundedCornerShape(12.dp)
    if (avatarPath.isNotEmpty()) {
        coil.compose.AsyncImage(model = java.io.File(avatarPath), contentDescription = name,
            contentScale = androidx.compose.ui.layout.ContentScale.Crop,
            modifier = Modifier.size(size.dp).clip(shape))
    } else {
        Box(Modifier.size(size.dp).clip(shape).background(InvertSurface()),
            contentAlignment = Alignment.Center) {
            Text(name.take(1), color = OnInvert(), fontSize = (size * 0.42f).sp,
                fontWeight = FontWeight.Bold)
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

// ---------- 好友动态 tab（P2：真信息流，bridge.moments_feed） ----------

data class MomentRow(val id: Long, val role: String, val name: String, val content: String,
                     val kind: String, val time: String, val likes: Int, val likedByMe: Boolean,
                     val comments: List<Triple<String, String, String>>)  // actor/content/time

@Composable
private fun MomentsFeed() {
    val bridge = remember { Python.getInstance().getModule("bridge") }
    val scope = rememberCoroutineScope()
    var rows by remember { mutableStateOf<List<MomentRow>?>(null) }
    var tick by remember { mutableStateOf(0) }
    var openComment by remember { mutableStateOf<Long?>(null) }
    var avatars by remember { mutableStateOf(mapOf<String, String>()) }
    fun load() = scope.launch {
        val o = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("moments_feed", 60).toString() })
        val arr = o.optJSONArray("moments") ?: org.json.JSONArray()
        rows = (0 until arr.length()).map { i ->
            val m = arr.getJSONObject(i)
            val cs = m.optJSONArray("comments") ?: org.json.JSONArray()
            MomentRow(m.getLong("id"), m.getString("role_id"), m.optString("name"),
                m.getString("content"), m.optString("kind"), m.optString("created_at"),
                m.optInt("likes"), m.optBoolean("liked_by_me"),
                (0 until cs.length()).map { j ->
                    val c = cs.getJSONObject(j)
                    Triple(c.optString("actor"), c.getString("content"), c.optString("created_at"))
                })
        }
        val rl = JSONObject(withContext(Dispatchers.IO) { bridge.callAttr("roles_list").toString() })
        rl.optJSONArray("roles")?.let { a ->
            avatars = (0 until a.length()).associate { i ->
                val r = a.getJSONObject(i); r.getString("id") to r.optString("avatar") } }
    }
    LaunchedEffect(tick) { load() }
    LaunchedEffect(openComment) { if (openComment != null) load() }
    Column(Modifier.fillMaxSize().background(PageBg()).statusBarsPadding()) {
        Text("好友动态", fontSize = 20.sp, fontWeight = FontWeight.SemiBold, color = PrimaryInk(),
            modifier = Modifier.fillMaxWidth().padding(vertical = 10.dp))
        val list = rows
        when {
            list == null -> Text("加载中…", color = Muted(), modifier = Modifier.padding(20.dp))
            list.isEmpty() -> Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally) {
                Spacer(Modifier.height(90.dp))
                Text("还没有动态", fontSize = 14.sp, color = Muted())
                Text("动态来自她们的虚拟生活——日程、心情、碎碎念，攒够了自然会有",
                    fontSize = 12.sp, color = MutedSoft())
            }
            else -> LazyColumn(Modifier.fillMaxSize()) {
                // 按日分组（本地日期）
                val grouped = list.groupBy { shortDate(it.time) }
                grouped.forEach { (day, ms) ->
                    item(key = "h_$day") {
                        Text(day, fontSize = 11.sp, color = MutedSoft(),
                            modifier = Modifier.padding(start = 20.dp, top = 14.dp, bottom = 4.dp))
                    }
                        items(ms, key = { "m_${it.id}" }) { m ->
                            MomentCard(m, avatars[m.role] ?: "",
                                onLike = {
                                    scope.launch {
                                        withContext(Dispatchers.IO) { bridge.callAttr("moment_like", m.id) }
                                        tick++
                                    }
                                },
                                onComment = { openComment = m.id })
                        }
                }
            }
        }
    }
    openComment?.let { mid ->
        MomentCommentDialog(mid, avatars,
            onSend = { text ->
                scope.launch {
                    withContext(Dispatchers.IO) { bridge.callAttr("moment_comment", mid, text) }
                    openComment = null; tick++
                }
            },
            onDismiss = { openComment = null })
    }
}

private fun shortDate(iso: String): String = try {
    val t = java.time.OffsetDateTime.parse(iso).atZoneSameInstant(java.time.ZoneId.systemDefault())
    val d = t.toLocalDate(); val now = java.time.LocalDate.now()
    when (d) { now -> "今天"; now.minusDays(1) -> "昨天"; else -> t.format(java.time.format.DateTimeFormatter.ofPattern("MM月dd日")) }
} catch (e: Exception) { "更早" }

@Composable
private fun MomentCard(m: MomentRow, avatar: String, onLike: () -> Unit, onComment: () -> Unit) {
    Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            RoleAvatar(m.name, avatar, size = 40)
            Spacer(Modifier.width(10.dp))
            Column(Modifier.weight(1f)) {
                Text(m.name, fontSize = 14.sp, fontWeight = FontWeight.Medium, color = PrimaryInk())
                Text(shortTime(m.time), fontSize = 10.sp, color = MutedSoft())
            }
        }
        Spacer(Modifier.height(8.dp))
        Text(m.content, fontSize = 15.sp, color = Body(), lineHeight = 22.sp,
            modifier = Modifier.padding(start = 50.dp))
        Spacer(Modifier.height(8.dp))
        // 互动行：[♡ n][评论] 两枚描边胶囊（Galaxy）
        Row(Modifier.padding(start = 50.dp), horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.clip(RoundedCornerShape(999.dp)).border(1.dp, CardBorder(), RoundedCornerShape(999.dp))
                    .background(if (m.likedByMe) InvertSurface() else Color.Transparent)
                    .clickable(onClick = onLike).padding(horizontal = 12.dp, vertical = 5.dp)) {
                Text((if (m.likedByMe) "♥ " else "♡ ") + (if (m.likes > 0) m.likes.toString() else "赞"),
                    fontSize = 12.sp, color = if (m.likedByMe) OnInvert() else Muted())
            }
            Box(Modifier.clip(RoundedCornerShape(999.dp)).border(1.dp, CardBorder(), RoundedCornerShape(999.dp))
                    .clickable(onClick = onComment).padding(horizontal = 12.dp, vertical = 5.dp)) {
                Text(if (m.comments.isEmpty()) "评论" else "评论 ${m.comments.size}",
                    fontSize = 12.sp, color = Muted())
            }
        }
        // 评论区（有则展开；她的回复=黑底反色小泡）
        if (m.comments.isNotEmpty()) {
            Column(Modifier.padding(start = 50.dp, top = 8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                m.comments.forEach { (actor, content, _) ->
                    val mine = actor == "user"
                    Text((if (mine) "我" else m.name) + "：" + content, fontSize = 12.sp,
                        color = if (mine) Body() else OnInvert(),
                        modifier = Modifier.background(if (mine) PageBg() else InvertSurface(),
                            RoundedCornerShape(8.dp)).padding(horizontal = 8.dp, vertical = 4.dp))
                }
            }
        }
    }
    HorizontalDivider(color = Hairline(), thickness = 0.5.dp, modifier = Modifier.padding(start = 66.dp))
}

@Composable
private fun MomentCommentDialog(momentId: Long, avatars: Map<String, String>,
                                onSend: (String) -> Unit, onDismiss: () -> Unit) {
    var text by remember { mutableStateOf("") }
    AlertDialog(onDismissRequest = onDismiss,
        confirmButton = {
            TextButton(onClick = { if (text.isNotBlank()) onSend(text.trim()) }, enabled = text.isNotBlank()) {
                Text("发送", color = PrimaryInk()) } },
        dismissButton = { TextButton(onClick = onDismiss) { Text("关闭", color = Muted()) } },
        title = { Text("评论", color = PrimaryInk(), fontSize = 16.sp) },
        text = { OutlinedTextField(text, { text = it }, Modifier.fillMaxWidth(), singleLine = true,
            placeholder = { Text("说点什么…", color = MutedSoft()) },
            colors = OutlinedTextFieldDefaults.colors(
                focusedBorderColor = PrimaryInk(), unfocusedBorderColor = Hairline())) },
        containerColor = CardBg())
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
    var settings by remember { mutableStateOf<org.json.JSONObject?>(null) }
    LaunchedEffect(role) {
        name = withContext(Dispatchers.IO) { bridge.callAttr("role_label", role).toString() }
        settings = JSONObject(withContext(Dispatchers.IO) {
            bridge.callAttr("moment_settings", role).toString() }).optJSONObject("settings")
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
            // ---- 行为设置（P2 最小集：主动/动态开关，写 role_settings） ----
            Text("行为", fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                color = MutedSoft(), modifier = Modifier.padding(bottom = 6.dp))
            val st = settings
            if (st != null) {
                val mo = st.optJSONObject("moments") ?: JSONObject()
                val pa = st.optJSONObject("proactive") ?: JSONObject()
                SettingSwitch("主动发消息", pa.optBoolean("enabled", true)) { on ->
                    scope.launch {
                        withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_set", role, "proactive", "enabled", if (on) "1" else "0")
                            settings = JSONObject(bridge.callAttr("moment_settings", role).toString())
                                .optJSONObject("settings")
                        }
                    }
                }
                SettingSeg("动态发布", listOf("关", "低", "中", "高"),
                    when {
                        !mo.optBoolean("enabled", true) -> 0
                        mo.optString("frequency", "medium") == "low" -> 1
                        mo.optString("frequency", "medium") == "high" -> 3
                        else -> 2
                    }) { idx ->
                    scope.launch {
                        withContext(Dispatchers.IO) {
                            if (idx == 0) bridge.callAttr("moment_set", role, "moments", "enabled", "0")
                            else {
                                bridge.callAttr("moment_set", role, "moments", "enabled", "1")
                                bridge.callAttr("moment_set", role, "moments", "frequency",
                                    listOf("", "low", "medium", "high")[idx])
                            }
                        }
                        settings = JSONObject(withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_settings", role).toString() }).optJSONObject("settings")
                    }
                }
                SettingSeg("动态中提及你", listOf("不提", "仅间接", "可以"),
                    when (mo.optString("mention_user", "indirect")) {
                        "no" -> 0; "yes" -> 2; else -> 1
                    }) { idx ->
                    scope.launch {
                        withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_set", role, "moments", "mention_user",
                                listOf("no", "indirect", "yes")[idx]) }
                        settings = JSONObject(withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_settings", role).toString() }).optJSONObject("settings")
                    }
                }
                // 动态类型过滤（P3：七型 chip 多选）
                val allowed = mo.optJSONArray("allowed_types")?.let { ta ->
                    (0 until ta.length()).map { ta.getString(it) } } ?:
                    listOf("D01", "D02", "D03", "D04", "D05", "D06", "D07")
                TypeChips(allowed) { next ->
                    scope.launch {
                        withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_set", role, "moments", "allowed_types",
                                org.json.JSONArray(next as Collection<*>).toString()) }
                        settings = JSONObject(withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_settings", role).toString() }).optJSONObject("settings")
                    }
                }
                Spacer(Modifier.height(10.dp))
                Text("互动", fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                    color = MutedSoft(), modifier = Modifier.padding(bottom = 4.dp))
                val ic = st.optJSONObject("interaction") ?: JSONObject()
                SettingSeg("评论回复风格", listOf("按人设", "极简", "不回复"),
                    when (ic.optString("comment_response_style", "character")) {
                        "minimal" -> 1; "none" -> 2; else -> 0
                    }) { idx ->
                    scope.launch {
                        withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_set", role, "interaction", "comment_response_style",
                                listOf("character", "minimal", "none")[idx]) }
                        settings = JSONObject(withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_settings", role).toString() }).optJSONObject("settings")
                    }
                }
                SettingSwitch("你点赞后私聊回应", ic.optBoolean("dm_after_like", false)) { on ->
                    scope.launch {
                        withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_set", role, "interaction", "dm_after_like", if (on) "1" else "0") }
                        settings = JSONObject(withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_settings", role).toString() }).optJSONObject("settings")
                    }
                }
                // 称呼与表达（P3）：固定称呼 + 表达强度
                Spacer(Modifier.height(10.dp))
                Text("称呼与表达", fontSize = 13.sp, fontWeight = FontWeight.SemiBold,
                    color = MutedSoft(), modifier = Modifier.padding(bottom = 4.dp))
                val ex = st.optJSONObject("expression") ?: JSONObject()
                NickField(ex.optString("fixed_nickname", "")) { v ->
                    scope.launch {
                        withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_set", role, "expression", "fixed_nickname", v) }
                    }
                }
                SettingSeg("表达强度", listOf("偏冷淡", "自然", "偏热情"),
                    when (ex.optString("expressiveness", "natural")) {
                        "cold" -> 0; "warm" -> 2; else -> 1
                    }) { idx ->
                    scope.launch {
                        withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_set", role, "expression", "expressiveness",
                                listOf("cold", "natural", "warm")[idx]) }
                        settings = JSONObject(withContext(Dispatchers.IO) {
                            bridge.callAttr("moment_settings", role).toString() }).optJSONObject("settings")
                    }
                }
                Spacer(Modifier.height(8.dp))
            }
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
        // 严重后果操作：必须输入角色名才放行（防误触；2026-09-01 用户裁决）
        var phrase by remember(mode) { mutableStateOf("") }
        val word = name  // 输入比对基准=角色显示名
        AlertDialog(onDismissRequest = { confirm = null },
            confirmButton = {
                TextButton(
                    enabled = phrase.trim() == word,
                    onClick = {
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
                            } else {
                                bridge.callAttr("mark_read", role)  // 清空后未读账已删，重推指针
                            }
                        }
                    }) { Text("确认", color = if (phrase.trim() == word) PrimaryInk() else MutedSoft()) }
            },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("取消", color = Muted()) } },
            title = { Text(if (mode == "intimacy") "重置与${name}的关系？" else "清空${name}的会话历史？",
                color = PrimaryInk()) },
            text = {
                Column {
                    Text(if (mode == "intimacy") "羁绊回到初见、作息偏移归零；共同的记忆库不受影响。此操作不可撤销。"
                          else "这段聊天记录将永久删除，不可恢复。", color = Muted())
                    Spacer(Modifier.height(12.dp))
                    Text("输入「$word」以确认：", fontSize = 12.sp, color = PrimaryInk())
                    OutlinedTextField(phrase, { phrase = it }, Modifier.fillMaxWidth().padding(top = 6.dp),
                        singleLine = true, placeholder = { Text(word, color = MutedSoft()) },
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = PrimaryInk(), unfocusedBorderColor = Hairline()))
                }
            },
            containerColor = CardBg())
    }
}

/** 动态类型多选 chip 组（D01-D07 中文标签） */
@Composable
private fun TypeChips(allowed: List<String>, onChange: (List<String>) -> Unit) {
    val all = listOf(
        "D01" to "日程", "D02" to "天气", "D03" to "心情", "D04" to "闪回",
        "D05" to "碎碎念", "D06" to "预告", "D07" to "关系")
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Text("动态类型", fontSize = 14.sp, color = PrimaryInk())
        Spacer(Modifier.height(6.dp))
        // 两行 flex
        all.chunked(4).forEach { rowItems ->
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 6.dp)) {
                rowItems.forEach { (code, label) ->
                    val on = code in allowed
                    Box(Modifier.clip(RoundedCornerShape(999.dp))
                            .border(1.dp, if (on) InvertSurface() else CardBorder(), RoundedCornerShape(999.dp))
                            .background(if (on) InvertSurface() else Color.Transparent)
                            .clickable {
                                onChange(if (on) allowed - code else allowed + code)
                            }
                            .padding(horizontal = 12.dp, vertical = 5.dp)) {
                        Text(label, fontSize = 12.sp, color = if (on) OnInvert() else Muted())
                    }
                }
            }
        }
    }
}

/** 固定称呼输入（失焦即存；留空=恢复自动演化） */
@Composable
private fun NickField(initial: String, onSave: (String) -> Unit) {
    var text by remember(initial) { mutableStateOf(initial) }
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Text("固定称呼（留空=随关系自动演化）", fontSize = 14.sp, color = PrimaryInk())
        Spacer(Modifier.height(6.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            OutlinedTextField(text, { text = it }, Modifier.weight(1f), singleLine = true,
                placeholder = { Text("自动", color = MutedSoft()) },
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = PrimaryInk(), unfocusedBorderColor = Hairline()))
            Spacer(Modifier.width(8.dp))
            TextButton(onClick = { onSave(text.trim()) }) { Text("存", color = PrimaryInk()) }
        }
    }
}

/** 行为设置行：标题+副题左，开关右（Galaxy 黑白：选中=反色） */
@Composable
private fun SettingSwitch(label: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, fontSize = 14.sp, color = PrimaryInk(), modifier = Modifier.weight(1f))
        Switch(checked, onCheckedChange = onChange,
            colors = SwitchDefaults.colors(
                checkedTrackColor = InvertSurface(), checkedThumbColor = OnInvert(),
                uncheckedTrackColor = CardBg(), uncheckedThumbColor = Muted(),
                uncheckedBorderColor = CardBorder()))
    }
}

/** 分段单选行：一段胶囊组（选中=黑底白字） */
@Composable
private fun SettingSeg(label: String, options: List<String>, selected: Int, onSelect: (Int) -> Unit) {
    Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
        Text(label, fontSize = 14.sp, color = PrimaryInk())
        Spacer(Modifier.height(6.dp))
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            options.forEachIndexed { i, t ->
                val sel = i == selected
                Box(Modifier.clip(RoundedCornerShape(999.dp))
                        .border(1.dp, if (sel) InvertSurface() else CardBorder(), RoundedCornerShape(999.dp))
                        .background(if (sel) InvertSurface() else Color.Transparent)
                        .clickable { onSelect(i) }
                        .padding(horizontal = 14.dp, vertical = 6.dp)) {
                    Text(t, fontSize = 12.sp, color = if (sel) OnInvert() else Muted())
                }
            }
        }
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
